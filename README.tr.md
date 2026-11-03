![Boreas: Linux için ASUS laptop kontrolü](docs/banner.jpg)

**Linux'ta ASUS ROG ve TUF dizüstüler için fan, CPU, GPU ve pil kontrolü.**

[English](README.md) · **Türkçe**

Boreas, Linux için açık kaynaklı bir Armoury Crate alternatifidir. ASUS ROG ve TUF dizüstülerde performans
modlarını, özel fan eğrilerini, CPU ve GPU güç limitlerini, GPU modlarını (Eco, Optimized ve Ultimate MUX
anahtarı), Aura klavye aydınlatmasını ve pil şarj limitini yönetir.

[asus-linux.org](https://asus-linux.org) topluluğunun çalışması üzerine kuruludur: donanım desteğinin
tamamı onların çekirdek sürücülerinden (asus-wmi, asus-armoury) gelir. Boreas, kullanıcı tarafındaki kısmı
(asusd, supergfxd, rog-control-center) tek bir küçük sistem servisi (`boreasd`) ve bir tepsi uygulamasıyla
(`boreas`) değiştirir.

![Boreas: açık tema, koyu tema, fanlar ve güç sayfası](docs/screenshots.png)

CachyOS kurulu bir ROG Strix G713PV (kernel 7.3, asus-armoury) üzerinde geliştirildi ve denendi; başka
modellerde ve dağıtımlarda farklı davranabilir.

**Diller:** English, Türkçe, 简体中文, हिन्दी, Español, العربية, Français, Português (Brasil), Русский,
Deutsch, 日本語.

## Özellikler

| Özellik | Boreas | Linux arayüzü |
|---|---|---|
| Sessiz / Dengeli / Turbo | ✓ | `platform_profile` |
| Özel modlar (Özel 1, 2…) | ✓ | aynı tabanda yerleşik moda bağlı |
| Mod başına fan eğrisi | ✓ (CPU, GPU, orta) | `asus_custom_fan_curve` hwmon |
| CPU Boost modu (mod başına) | ✓ (`Varsayılan` / `Kapalı` / `Verimli` / `Dengeli` / `Agresif` / `Maks`) | `cpufreq/boost`, `energy_performance_preference` |
| Güç limitleri (SPL/SPPT/FPPT) | ✓ | asus-armoury `ppt_*` |
| NVIDIA Dynamic Boost / sıcaklık hedefi | ✓ | asus-armoury `nv_*` |
| CPU sıcaklık hedefi (75-95 °C, mod başına) | Aralığı bildirilen ASUS CPU güç kontrolleriyle | asus-armoury üzerinden yazılımsal kontrol |
| Pil/prize göre otomatik mod | ✓ | `power_supply` |
| GPU: Eco / Standart / Optimized / Ultimate | ✓ | `dgpu_disable`, `gpu_mux_mode` |
| Ekran 60 Hz / maks / Otomatik | ✓ (KDE) | `kscreen-doctor` |
| Panel overdrive | ✓ | `panel_overdrive` |
| Klavye Aura (statik, nefes, döngü, gökkuşağı, strobe) | ✓ | hidraw, rapor 0x5D (output rapor) |
| Klavye parlaklığı | ✓ | `asus::kbd_backlight` |
| Şarj limiti | ✓ | `charge_control_end_threshold` |
| Fn+F5 / KDE güç profili | ✓ | servis profil değişimini izler, mod ayarlarını yeniden uygular |
| Uykudan dönüşte yeniden uygulama | ✓ | systemd-sleep kancası |

Henüz yok: AniMe/Slash matrix, M4/ROG tuşu ataması (KDE kısayoluna `boreasctl mode next`
bağlanabilir), GPU saat ofsetleri, Ryzen undervolt (Curve Optimizer), çevre birimleri (fare, kulaklık),
fan histerezisi, klavye ışığının uyku/kapanış ayarları (Aura power), prize/pile göre ayrı
klavye parlaklığı, tek seferlik %100 şarj, açılış sesi (`boot_sound`), USB-C şarj modu,
Aura ikinci renk.

## Kurulum

Gereksinimler: systemd kullanan bir dağıtım, asus-wmi / asus-armoury sürücüleri olan bir çekirdek,
Python 3.11+, PySide6 (arayüz için), KDE'de ekran yenileme hızı için `kscreen-doctor` ve Wayland'de keskin
görüntü için Qt'nin Wayland eklentisi. Python ve PySide6'yı dağıtımınızın paket yöneticisiyle kurun
(Arch / CachyOS'ta: `sudo pacman -S --needed python pyside6 qt6-wayland`).

```bash
git clone https://github.com/Yusuf-Yonturk/boreas.git
cd boreas
sudo ./install.sh
```

İsteğe bağlı eBPF GPU yardımcısı (`boreas-gpu-engine`) depoda tutulmaz: CI onu Rust kaynağından
derleyip her sürümün GitHub Release'ine ekler; kurulum betiği kurulan sürüm için indirir ve hiçbir
şey yazmadan önce sha256 sağlama toplamını denetler. Çevrimdışı kurmak ya da kendi derlemenizi kurmak
için dosyayı gösterin: `sudo BOREAS_GPU_ENGINE=/yol/boreas-gpu-engine ./install.sh`.
Bkz. [docs/gpu-engine.md](docs/gpu-engine.md).

Kurulum arayüz dilini sorar (Enter = English). Sormadan kurmak için: `sudo BOREAS_LANG=tr ./install.sh`.
Her kullanıcı dili sonradan penceredeki küre düğmesinden değiştirebilir. Çince ve Japonca için
`noto-fonts-cjk`, Hintçe ve Arapça için `noto-fonts` gerekir.

Betik `asusd`, `asus-shutdown`, `supergfxd` ve `power-profiles-daemon`'ı maskeler (aynı sysfs
dosyalarına yazıyorlar; asusd udev kuralıyla, power-profiles-daemon masaüstü tarafından D-Bus ile
her açılışta başlatıldığı için `disable` yetmez), `boreasd`'yi açar. Masaüstünün kendi güç profili
menüsü (KDE pil simgesi, GNOME hızlı ayarlar) bu yüzden boş görünür; modu Boreas yönetir. Kontrolü
geçici olarak geri vermek için:
`sudo systemctl stop boreasd && sudo systemctl unmask --now power-profiles-daemon`.
İlk açılışta servis mevcut şarj limitini, klavye parlaklığını, modu ve GPU modunu benimser;
donanıma hiçbir şey yazmaz.

Geri almak: `sudo ./uninstall.sh` (maskeler kaldırılır, asusd / supergfxd / power-profiles-daemon geri açılır).

KDE'de Ayarlar → Güç Yönetimi'nde prize ve pile ayrı güç profili seçiliyse Boreas'ın
"pil/prize göre otomatik mod"uyla çakışır; ikisinden birini kapatın.

## Kullanım

```bash
boreas                 # arayüz (tepsi)
boreasctl status
boreasctl mode turbo   # sessiz | dengeli | turbo | next | prev
boreasctl gpu eco      # eco | standard | optimized | ultimate
boreasctl charge 80
boreasctl cputemp 85   # etkin mod; default = firmware varsayılanı
```

Arayüz sistemin açık/koyu temasını izler; başlıktaki düğme otomatik, açık ve koyu arasında geçer.
Zorlamak için `BOREAS_THEME=light boreas` ya da `dark`. `BOREAS_SCALE=1` arayüz boyutunu değiştirir
(varsayılan `0.78`), `BOREAS_LANG=tr boreas` dili zorlar. Sağ üstteki **Fanlar ve güç** düğmesi aynı pencerede fan eğrileri,
güç limitleri ve pil sayfasını açar; tekrar basınca ana sayfaya döner.

CPU sıcaklık hedefi artık ASUS'un `asus-armoury` güç arayüzü üzerinden yönetilir.
Boreas hedefe yaklaşınca CPU'nun SPL ve mevcut SPPT/FPPT watt sınırlarını azaltır.
Beş saniye yeterince soğuk kalınca 1 W artırır; seçili profil ve kullanıcı güç
ayarlarından alınan başlangıç tavanlarını aşmaz. SMU/PCI erişimine geri dönmez.
BIOS sürüm listesi veya özel kernel modülü gerektirmez; geçerli güç aralıkları,
SPL kontrolü ve CPU sıcaklık sensörü gerekir. Her ASUS destekleniyor demek değildir.

Güç kontrolü için özel fan eğrisi gerektiren modellerde, kullanıcı eğrisi yoksa
profilin fabrika eğrileri etkinleştirilir. Varsayılan seçimi kontrolü kapatır ve
hâlâ Boreas'ın yönettiği watt değerlerini geri yükler. Profil ve uyku dönüşünde
kontrol yeniden hazırlanır. Sensör kaybı, yazma hatası veya dışarıdan güç değişimi
kontrolü durdurur ve hata gösterir; otomatik SMU geçişi yapılmaz.

`boreasctl cputemp 85` etkin modun hedefini ayarlar; `cputemp default` kapatır.
Durumda `control: power-feedback`, `target`, `state`, `error`, `ceilings_w` ve
`requested_w` bulunur. `applied` null'dır; donanıma sıcaklık limiti yazılmaz.
Eski kayıtlı hedefler kullanılır, önceki SMU ayarı bu yöntemle sıfırlanmaz.

**Bu kesin sıcaklık tavanı değildir.** Kısa süreli aşım olabilir; en düşük güçte
bile hedefe ulaşılamazsa `limited` durumu bildirilir. İsteğe bağlı deneysel çekirdek
SMU köprüsü (`smu-bridge`) yalnızca belgelenen G713PV / G713PV.336 yapılandırmasıyla
sınırlıdır; genel ASUS SMU desteği değildir. Başka bir güç
ayarlama servisiyle birlikte çalıştırmayın. Ayrıntılar: [CPU kontrolü](docs/cpu-temperature.md).

## Güncelleme

Boreas açıldıktan 10 saniye sonra, ardından günde bir kez GitHub'a bakar; her seferinde yalnızca sürüm
numarasının yazdığı küçük dosyayı indirir. Yeni bir sürüm varsa pencerede **Güncelle · Sürüm notları ·
Gizle** görünür. **Güncelle** son sürümü GitHub'dan indirir, yönetici şifrenizi sorduktan sonra (pkexec)
kurar ve uygulamayı yeniden başlatır. **Sürüm notları** [CHANGELOG.md](CHANGELOG.md) dosyasını açar.

Elle güncellemek için:

```bash
cd boreas
git pull
sudo ./install.sh
```

Ardından pencerede **Çıkış**'a basıp `boreas` ile yeniden açın. Kontrol tepsi menüsünden
(**Güncellemeleri denetle**) ya da `BOREAS_NO_UPDATE_CHECK=1` ile kapatılabilir.

## GPU modları hakkında

- **Ultimate** ↔ diğerleri MUX'u değiştirir, yeniden başlatma gerekir.
- **Eco → Standard** yeniden başlatmadan hemen geçer.
- **Standard → Eco**: dGPU sürücüsü bağlıyken `dgpu_disable` yazılırsa sistem kilitlenebilir. Servis
  önce nvidia-persistenced ve nvidia-powerd'yi durdurur, NVIDIA modüllerini kaldırır ve PCI aygıtını
  çıkarır. dGPU kullanımdaysa (KWin, oyun, CUDA) Eco ertelenir: servis 15 saniyede bir yeniden dener ve
  açılışta ekran yöneticisinden önce uygular. KDE Plasma'da (Wayland) KWin dGPU'yu oturum boyunca açık
  tuttuğu için Eco orada bir sonraki açılışta uygulanır. NVIDIA modülü hiç bırakılmıyorsa yeniden
  başlatmaya kadar tekrar denemez.

## Deneysel gömülü GPU motoru

Cardwire kodundan uyarlanmış isteğe bağlı bir eBPF arka ucu (`boreas-gpu-engine`) vardır.
`sudo ./install.sh` bu sürüm için CI'ın derlediği x86_64 yardımcısını GitHub Release'ten indirip
sha256 denetiminden sonra kurar; `BOREAS_GPU_ENGINE` ile verilen dosya öncelik alır (varsayılan
olarak kapalı kalır).
Arayüzdeki **GPU modu** kartında bulunan **eBPF motoru (yeniden başlatmadan Eco) · Deneysel** anahtarıyla
veya komut satırından geçiş yapabilirsiniz:

```bash
boreasctl gpu-backend ebpf       # eBPF GPU motorunu etkinleştir
boreasctl gpu-backend firmware   # varsayılan ASUS firmware yöntemine dön
```

Özel/yamalı motorunuzu güncellemelerden korumak için `/etc/boreas/boreas-gpu-engine`
konumuna kurun; Boreas bu dosyaya öncelik verir ve protokol uyumunu denetler.
Kurulum, kaydettiği sağlama toplamına göre değiştirilmiş motorları da korur;
sağlama toplamı bilinmeyen eski dosyaları otomatik etkinleştirmeden yedekler.
Eski systemd ortam değişkeni varsa UI seçimini kullanmadan önce kaldırılmalıdır.
Firmware’e dönüş **Standart** modunu seçer; Ultimate ve bekleyen MUX isteği korunur.

Eco politikası tek başına GPU’nun uyuduğunu göstermez. 2 Kasım 2026 tarihli G713PV
canlı testinde Linux `7.3.0-5-cachyos` üzerinde erişim engelleme ve geri açma başarılı
oldu; ancak GPU 60 saniye sonra `D0 / active` kaldı ve RTD3 `Not supported` bildirdi.
Bu sonuç güç tasarrufu veya tüm cihazlarda uyumluluk garantisi değildir.

[Derleme, sınırlamalar ve doğrulama adımları](docs/gpu-engine.md).

## Geliştirme

```bash
python3 -m unittest discover -s tests   # sahte sysfs ağacıyla, root gerekmez
python3 -m boreas.gui.app               # arayüz (servis çalışıyor olmalı)
```

Ayarlar: `/etc/boreas/config.json`. Soket: `/run/boreas.sock` (`wheel` grubu).

Yeni sürüm yayınlamak için `boreas/__init__.py` içindeki `__version__` değerini artırın, [CHANGELOG.md](CHANGELOG.md)
dosyasına bir bölüm ekleyin, `main`'e gönderin ve `v<sürüm>` etiketini gönderin; CI GPU yardımcısını o sürümün
GitHub Release'ine ekler, kurulum betiği oradan indirir. Kurulu kopyalar bir gün içinde güncelleme notunu gösterir.

## Katkıda bulunma

Katkıda bulunmaktan çekinmeyin! Hata bildirimleri, çeviriler ve pull request'ler memnuniyetle
karşılanır; özellikle ROG Strix G713PV dışında bir ASUS laptopunuz varsa. Çalışma adımları, testler ve
yeni dil ekleme için [CONTRIBUTING.tr.md](CONTRIBUTING.tr.md) dosyasına bakın.

## Teşekkürler

- Deneysel Rust motorunda kullanılan GPL-3.0 GPU erişim kontrolü kodu için
  luytan, katkıcıları ve [Cardwire](https://github.com/OpenGamingCollective/cardwire) projesine.
  [Üçüncü taraf bildirimleri](THIRD_PARTY_NOTICES.md).

- ASUS donanımı üzerine yaptıkları açık kaynak çalışma için Serge'ye ve
  [G-Helper](https://github.com/seerge/g-helper) topluluğuna.
- Linux çekirdek sürücüleri (asus-wmi, asus-armoury) ve asusctl için [asus-linux.org](https://asus-linux.org)
  topluluğuna.
- AMD SMU araştırmaları için [RyzenAdj](https://github.com/FlyGoat/RyzenAdj) ve
  [ZenStates-Core](https://github.com/irusanov/ZenStates-Core) projelerine.

## Lisans

Copyright (C) 2026 Yusuf Yontürk. [GPL-3.0](LICENSE) lisansıyla dağıtılır.

ASUS, ROG, TUF ve Armoury Crate, ASUSTeK Computer Inc.'in ticari markalarıdır; bu proje ASUS ile bağlantılı değildir.
