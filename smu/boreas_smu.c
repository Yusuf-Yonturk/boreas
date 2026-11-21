// SPDX-License-Identifier: GPL-2.0-only
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/acpi.h>
#include <linux/build_bug.h>
#include <linux/capability.h>
#include <linux/delay.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/ioctl.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/types.h>
#include <linux/uaccess.h>
#include <linux/version.h>

#include <asm/processor.h>
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 16, 0)
#include <asm/amd/node.h>
#else
#include <asm/amd_nb.h>
#endif

#define BOREAS_SMU_ABI		2
#define BOREAS_SMU_GET_VERSION	1
#define BOREAS_SMU_SET_TCTL	2
#define BOREAS_SMU_VERSION_CMD	0x02
#define BOREAS_SMU_OK		0x01
#define BOREAS_SMU_TIMEOUT_MS	200
/*
 * Once our command is written, the BIOS must not get the mailbox back before the
 * reply: its A018 method clears the response and writes without a ready-check.
 * So the reply wait is long, and a mailbox that never answers is not used again.
 */
#define BOREAS_SMU_REPLY_TIMEOUT_MS 5000
#define BOREAS_SMU_ACPI_TIMEOUT_MS 500
#define BOREAS_SMU_ACPI_PATH	"\\_SB.AM00"

struct boreas_smu_req {
	__u32 abi;
	__u32 op;
	__u32 arg;
	__u32 status;
	__u32 result;
	__u32 reserved[3];
};

#define BOREAS_SMU_IOC _IOWR('B', 0x74, struct boreas_smu_req)

struct boreas_smu_group {
	const char *name;
	u32 tctl_cmd;
	u32 command;
	u32 response;
	u32 argument0;
};

enum boreas_smu_group_id {
	BOREAS_GROUP_A,
	BOREAS_GROUP_B,
	BOREAS_GROUP_C,
	BOREAS_GROUP_D,
	BOREAS_GROUP_E,
};

static const struct boreas_smu_group boreas_smu_groups[] = {
	[BOREAS_GROUP_A] = {
		.name = "Raven/Picasso/Dali",
		.tctl_cmd = 0x1f,
		.command = 0x03b10528,
		.response = 0x03b10564,
		.argument0 = 0x03b10998,
	},
	[BOREAS_GROUP_B] = {
		.name = "Renoir/Lucienne/Cezanne",
		.tctl_cmd = 0x19,
		.command = 0x03b10528,
		.response = 0x03b10564,
		.argument0 = 0x03b10998,
	},
	[BOREAS_GROUP_C] = {
		.name = "VanGogh/Mendocino/Rembrandt/Phoenix/HawkPoint",
		.tctl_cmd = 0x19,
		.command = 0x03b10528,
		.response = 0x03b10578,
		.argument0 = 0x03b10998,
	},
	[BOREAS_GROUP_D] = {
		.name = "DragonRange/Raphael/FireRange/GraniteRidge",
		.tctl_cmd = 0x3f,
		.command = 0x03b10530,
		.response = 0x03b1057c,
		.argument0 = 0x03b109c4,
	},
	[BOREAS_GROUP_E] = {
		.name = "StrixPoint/Krackan/StrixHalo",
		.tctl_cmd = 0x19,
		.command = 0x03b10928,
		.response = 0x03b10978,
		.argument0 = 0x03b10998,
	},
};

static const struct boreas_smu_cpu {
	u8 family;
	u8 model;
	enum boreas_smu_group_id group;
} boreas_smu_cpus[] = {
	{ 0x17, 0x11, BOREAS_GROUP_A },
	{ 0x17, 0x18, BOREAS_GROUP_A },
	{ 0x17, 0x20, BOREAS_GROUP_A },
	{ 0x17, 0x60, BOREAS_GROUP_B },
	{ 0x17, 0x68, BOREAS_GROUP_B },
	{ 0x19, 0x50, BOREAS_GROUP_B },
	{ 0x17, 0x90, BOREAS_GROUP_C },
	{ 0x17, 0x91, BOREAS_GROUP_C },
	{ 0x17, 0xa0, BOREAS_GROUP_C },
	{ 0x19, 0x40, BOREAS_GROUP_C },
	{ 0x19, 0x44, BOREAS_GROUP_C },
	{ 0x19, 0x74, BOREAS_GROUP_C },
	{ 0x19, 0x75, BOREAS_GROUP_C },
	{ 0x19, 0x78, BOREAS_GROUP_C },
	{ 0x19, 0x7c, BOREAS_GROUP_C },
	{ 0x19, 0x61, BOREAS_GROUP_D },
	{ 0x1a, 0x44, BOREAS_GROUP_D },
	{ 0x1a, 0x20, BOREAS_GROUP_E },
	{ 0x1a, 0x24, BOREAS_GROUP_E },
	{ 0x1a, 0x60, BOREAS_GROUP_E },
	{ 0x1a, 0x68, BOREAS_GROUP_E },
	{ 0x1a, 0x70, BOREAS_GROUP_E },
};

static const struct boreas_smu_group *boreas_smu_group;
static acpi_handle boreas_smu_acpi_mutex;
static DEFINE_MUTEX(boreas_smu_mutex);

static int boreas_smu_lock(void)
{
	acpi_status status;

	/* A018 uses this AML mutex for the same MP1 mailbox. */
	status = acpi_acquire_mutex(boreas_smu_acpi_mutex, NULL,
				    BOREAS_SMU_ACPI_TIMEOUT_MS);
	if (ACPI_FAILURE(status))
		return -EBUSY;

	mutex_lock(&boreas_smu_mutex);
	return 0;
}

static void boreas_smu_unlock(void)
{
	mutex_unlock(&boreas_smu_mutex);
	acpi_release_mutex(boreas_smu_acpi_mutex, NULL);
}

static bool boreas_smu_wedged;

static int boreas_smu_poll(u32 *response, unsigned int timeout_ms)
{
	ktime_t deadline = ktime_add_ms(ktime_get(), timeout_ms);

	while (ktime_before(ktime_get(), deadline)) {
		if (amd_smn_read(0, boreas_smu_group->response, response))
			return -EIO;
		if (*response)
			return 0;
		usleep_range(500, 1000);
	}

	return -ETIMEDOUT;
}

static int boreas_smu_xfer(u32 command, u32 argument, u32 *status, u32 *result)
{
	u32 response;
	unsigned int i;
	int ret;

	ret = boreas_smu_lock();
	if (ret)
		return ret;

	if (boreas_smu_wedged) {
		ret = -EIO;
		goto out;
	}

	/* An unready mailbox must time out without any writes. */
	ret = boreas_smu_poll(&response, BOREAS_SMU_TIMEOUT_MS);
	if (ret)
		goto out;

	if (amd_smn_write(0, boreas_smu_group->response, 0)) {
		ret = -EIO;
		goto out;
	}

	for (i = 0; i < 6; i++) {
		if (amd_smn_write(0, boreas_smu_group->argument0 + 4 * i,
				  i == 0 ? argument : 0)) {
			ret = -EIO;
			goto out;
		}
	}

	if (amd_smn_write(0, boreas_smu_group->command, command)) {
		ret = -EIO;
		goto out;
	}

	ret = boreas_smu_poll(status, BOREAS_SMU_REPLY_TIMEOUT_MS);
	if (ret) {
		boreas_smu_wedged = true;
		pr_warn("no reply to command 0x%x within %u ms, mailbox not used again\n",
			command, BOREAS_SMU_REPLY_TIMEOUT_MS);
		goto out;
	}

	if (amd_smn_read(0, boreas_smu_group->argument0, result))
		ret = -EIO;
out:
	boreas_smu_unlock();
	return ret;
}

static long boreas_smu_ioctl(struct file *file, unsigned int cmd,
			     unsigned long arg)
{
	struct boreas_smu_req req;
	void __user *user_req = (void __user *)arg;
	u32 command, argument;
	int ret;

	if (!capable(CAP_SYS_ADMIN))
		return -EPERM;
	if (cmd != BOREAS_SMU_IOC)
		return -ENOTTY;
	if (copy_from_user(&req, user_req, sizeof(req)))
		return -EFAULT;
	if (req.abi != BOREAS_SMU_ABI || req.reserved[0] ||
	    req.reserved[1] || req.reserved[2])
		return -EINVAL;

	switch (req.op) {
	case BOREAS_SMU_GET_VERSION:
		if (req.arg)
			return -EINVAL;
		command = BOREAS_SMU_VERSION_CMD;
		argument = 1;
		break;
	case BOREAS_SMU_SET_TCTL:
		if (req.arg < 75 || req.arg > 96)
			return -EINVAL;
		command = boreas_smu_group->tctl_cmd;
		argument = req.arg;
		break;
	default:
		return -EINVAL;
	}

	ret = boreas_smu_xfer(command, argument, &req.status, &req.result);
	if (ret)
		return ret;
	if (copy_to_user(user_req, &req, sizeof(req)))
		return -EFAULT;

	return 0;
}

static const struct file_operations boreas_smu_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = boreas_smu_ioctl,
	.compat_ioctl = compat_ptr_ioctl,
};

static struct miscdevice boreas_smu_device = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "boreas-smu",
	.fops = &boreas_smu_fops,
	.mode = 0600,
};

static int __init boreas_smu_detect_cpu(void)
{
	unsigned int i;

	if (boot_cpu_data.x86_vendor == X86_VENDOR_AMD) {
		for (i = 0; i < ARRAY_SIZE(boreas_smu_cpus); i++) {
			if (boreas_smu_cpus[i].family == boot_cpu_data.x86 &&
			    boreas_smu_cpus[i].model == boot_cpu_data.x86_model) {
				boreas_smu_group =
					&boreas_smu_groups[boreas_smu_cpus[i].group];
				return 0;
			}
		}
	}

	pr_info("unsupported CPU vendor %u family 0x%x model 0x%x, not loading\n",
		boot_cpu_data.x86_vendor, boot_cpu_data.x86,
		boot_cpu_data.x86_model);
	return -ENODEV;
}

static const char * __init boreas_smu_status_name(u32 status)
{
	switch (status) {
	case 0xff:
		return "failed";
	case 0xfe:
		return "unknown command";
	case 0xfd:
		return "rejected (prerequisite)";
	case 0xfc:
		return "busy";
	default:
		return "unrecognized response";
	}
}

static int __init boreas_smu_init(void)
{
	acpi_status acpi_ret;
	u32 response, version;
	int ret;

	BUILD_BUG_ON(sizeof(struct boreas_smu_req) != 32);
	BUILD_BUG_ON(BOREAS_SMU_IOC != 0xc0204274U);

	ret = boreas_smu_detect_cpu();
	if (ret)
		return ret;

	acpi_ret = acpi_get_handle(NULL, BOREAS_SMU_ACPI_PATH,
				   &boreas_smu_acpi_mutex);
	if (ACPI_FAILURE(acpi_ret)) {
		pr_info("no \\_SB.AM00 mutex, not loading\n");
		return -ENODEV;
	}

	ret = boreas_smu_lock();
	if (ret) {
		pr_info("initial mailbox lock failed: %d, not loading\n", ret);
		return ret;
	}
	ret = amd_smn_read(0, boreas_smu_group->response, &response);
	boreas_smu_unlock();
	if (ret) {
		pr_info("initial mailbox response read failed, not loading\n");
		return -EIO;
	}
	pr_info("initial MP1 response: 0x%08x\n", response);

	ret = boreas_smu_xfer(BOREAS_SMU_VERSION_CMD, 1, &response, &version);
	if (ret) {
		pr_info("SMU version query transport failed: %d, not loading\n", ret);
		return ret;
	}
	if (response != BOREAS_SMU_OK) {
		pr_info("SMU version query status 0x%08x (%s), not loading\n",
			response, boreas_smu_status_name(response));
		return -ENODEV;
	}

	pr_info("%s, family 0x%x model 0x%x, SMU %u.%u.%u\n",
		boreas_smu_group->name, boot_cpu_data.x86,
		boot_cpu_data.x86_model, (version >> 16) & 0xff,
		(version >> 8) & 0xff, version & 0xff);

	return misc_register(&boreas_smu_device);
}

static void __exit boreas_smu_exit(void)
{
	/* Preserve the current ceiling; unloading must not write to the SMU. */
	misc_deregister(&boreas_smu_device);
}

module_init(boreas_smu_init);
module_exit(boreas_smu_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Boreas AMD Ryzen SMU temperature ceiling control");
MODULE_AUTHOR("Boreas project");
