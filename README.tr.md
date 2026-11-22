![Boreas: Linux için ASUS laptop kontrolü](docs/banner.png)

**Linux'ta ASUS ROG ya da TUF dizüstünüz için tek küçük pencere: her mod kendi fanlarını, güç limitlerini ve sıcaklık hedefini taşır.**

[English](README.md) · **Türkçe**

<p align="center">
  <img src="docs/demo.gif" alt="Boreas: açık ve koyu temada ana pencere ile Fanlar ve güç sayfası; mod, GPU modu, klavye efekti ve şarj limiti değişirken">
</p>

Boreas, Linux'ta Armoury Crate'in yerini alır: performans modları, özel fan eğrileri, CPU ve GPU güç
limitleri, GPU modları (Eco, Optimized ve Ultimate MUX anahtarı), Aura klavye aydınlatması ve pil şarj
limiti için tek küçük pencere. Her mod kendi fanlarını, güç
limitlerini ve CPU sıcaklık hedefini taşır; tek tıkla bütün makinenin davranışı değişir. Özgür ve açık
kaynak.

[asus-linux.org](https://asus-linux.org) topluluğunun çalışması üzerine kuruludur: donanım desteğinin
büyük kısmı çekirdeğin asus-wmi sürücüsünden (asus-linux.org geliştiricilerinin genişlettiği) ve onların
asus-armoury sürücüsünden gelir. Boreas, kullanıcı tarafındaki kısmı
(asusd, supergfxd, rog-control-center) tek bir küçük sistem servisi (`boreasd`) ve bir tepsi uygulamasıyla
(`boreas`) değiştirir; Ryzen'de CPU sıcaklık tavanı için kendi küçük çekirdek modülünü ekler.

Boreas, Linux için Python ve Qt ile, küçük bir C çekirdek modülü ve bir Rust GPU yardımcısıyla sıfırdan
yazıldı. Üçüncü taraf kodu yalnız isteğe bağlı eBPF GPU motorunda bulunur; bu motor Cardwire'ı (GPL-3.0)
kullanır. Boreas'ın hangi donanım ayrıntılarını başka projelerden öğrendiği
[üçüncü taraf bildirimlerinde](THIRD_PARTY_NOTICES.md) yazılıdır. Arayüz, Windows'ta
[G-Helper](https://github.com/seerge/g-helper) kullananlara tanıdık gelecektir; Boreas ise Linux için
bağımsız olarak geliştirilmiştir.

## Hızlı başlangıç

```bash
git clone https://github.com/Yusuf-Yonturk/boreas.git && cd boreas && sudo ./install.sh
```

Sonra uygulama menüsünden **Boreas**'ı açın ya da `boreas` çalıştırın. Gereksinimler
[Kurulum](#kurulum) bölümünde, [kurulumun neyi değiştirdiği](#kurulum-sisteminizde-neyi-değiştirir) ile
birlikte yazılıdır; `sudo ./uninstall.sh` Boreas'ı kaldırır ve maskelediği servisleri geri getirir, ayarları
`/etc/boreas` içinde kalır.

## Neden Boreas

- **Tek pencere, tek servis.** Kullanıcı tarafının tamamı `boreasd` ve bir tepsi uygulamasıdır; asusd,
  supergfxd ve rog-control-center'ın yerini alır. Temel ayarlar komut satırında da vardır (`boreasctl`: mod, GPU modu, şarj limiti, klavye parlaklığı,
  sıcaklık hedefi ve tabanı); fan eğrileri, güç limitleri, CPU Boost, güç profili ve klavye efektleri
  pencereden ayarlanır.
- **Mod eksiksiz bir ön ayardır.** Fan eğrileri, güç limitleri, CPU Boost, güç profili ve sıcaklık hedefi
  mod başına saklanır; Sessiz / Dengeli / Turbo'nun yanına kendi modlarınızı ekleyebilirsiniz. Mod
  değişince hepsi birlikte değişir: pencereden, tepsiden, Fn+F5 ile ya da pil / prize göre otomatik.
- **CPU sıcaklık hedefi.** Bir mod için 75-95 °C seçin. Desteklenen AMD Ryzen dizüstülerinde hedefi
  CPU'nun kendisi tutar; diğerlerinde Boreas güç limitlerini o hedefe göre yönetir; isteğe bağlı bir güç tabanı da
  oyun modunun firmware'in minimumuna düşmesini önler.
- NVIDIA dGPU'lu dizüstülerde **yeniden başlatmadan Eco**, isteğe bağlı eBPF GPU arka ucuyla (deneysel).
- **11 dil, açık ve koyu tema, uygulama içi güncelleme**; Windows'tan gelenlere tanıdık bir düzenle.
- **Denemesi kolay, geri alması kolay.** İlk açılışta Boreas mevcut şarj limitinizi, klavye parlaklığınızı,
  modunuzu ve GPU modunuzu devralır; fan eğrileri, güç limitleri ve CPU Boost firmware varsayılanlarından
  başlar. `sudo ./uninstall.sh` asusd, supergfxd ve power-profiles-daemon'ı geri getirir; Boreas'ın ayarları
  `/etc/boreas` içinde kalır.

**Boreas mı, asusctl mi?** Her modun kendi fanlarını, güç limitlerini ve sıcaklık hedefini taşıdığı tek
bir pencere istiyorsanız Boreas'ı seçin. AniMe Matrix ya da Slash ekranı gerekiyorsa
asus-linux.org projesinin kendi aracı olan [asusctl](https://github.com/OpenGamingCollective/asusctl)'i
seçin. İkisi aynı çekirdek dosyalarına yazdığı için aynı anda yalnız biri çalışabilir; kurulum asusd ve
supergfxd'yi maskeler, kaldırma betiği geri getirir.

## Denenen donanım

| Model | Dağıtım, çekirdek | Durum |
|---|---|---|
| ROG Strix G713PV | CachyOS, kernel 7.3 (asus-armoury) | burada geliştirildi ve denendi |

Boreas çekirdeğin standart asus-wmi / asus-armoury arayüzleri üzerine kuruludur; bu yüzden tek bir model için
yazılmadı. Bulamadığı donanımın kartları (GPU modları, panel overdrive, yenileme hızı) gizli kalır.
Sıradaki satır sizin modeliniz olabilir: Boreas'ı çalıştırın, sonra her şey çalışsa da çalışmasa da bir
[donanım raporu](https://github.com/Yusuf-Yonturk/boreas/issues/new?template=hardware_report.yml) gönderin.

## Özellikler

| Özellik | Boreas | Linux arayüzü |
|---|---|---|
| Sessiz / Dengeli / Turbo | ✓ | `platform_profile` |
| Özel modlar (Özel 1, 2…) | ✓ | bir yerleşik modu temel alır (aynı `platform_profile`) |
| Mod başına fan eğrisi | ✓ (CPU, GPU, orta) | `asus_custom_fan_curve` hwmon |
| CPU Boost modu (mod başına) | ✓ (`Varsayılan` / `Kapalı` / `Verimli` / `Dengeli` / `Agresif` / `Maks`) | `cpufreq/boost`, `energy_performance_preference` |
| Güç profili (mod başına) | ✓ (`Varsayılan` / `Güç tasarrufu` / `Performans`): CPU frekans governor'ı; Boreas power-profiles-daemon'ı maskelemeden önce bunu masaüstünün güç menüsü ayarlıyordu | `cpufreq/scaling_governor` |
| Güç limitleri (SPL/SPPT/FPPT) | ✓ | asus-armoury `ppt_*` |
| NVIDIA Dynamic Boost / sıcaklık hedefi | ✓ | asus-armoury `nv_*` |
| CPU sıcaklık hedefi (75-95 °C, mod başına) | Aralığı bildirilen ASUS CPU güç kontrolleriyle ya da CPU'nun kendi tavanıyla (Ryzen) | asus-armoury üzerinden yazılımsal geri besleme; desteklenen Ryzen dizüstülerinde CPU'nun kendi tavanı (çekirdek modülü, varsayılan açık) |
| Pil/prize göre otomatik mod | ✓ | `power_supply` |
| GPU: Eco / Standart / Optimized / Ultimate | ✓ (Eco ve Optimized NVIDIA dGPU ile) | `dgpu_disable`, `gpu_mux_mode` |
| Ekran: en düşük hız / maks / Otomatik (Otomatik, panel overdrive'ı da prize göre açıp kapatır) | ✓ (KDE) | `kscreen-doctor` |
| Panel overdrive | ✓ | `panel_overdrive` |
| Klavye Aura (statik, iki renkli nefes, döngü, strobe; tek bölgeli klavyeler dışında gökkuşağı; tuş başına aydınlatmalı klavyelerde Star, Rain, Highlight, Laser, Ripple, Comet, Flash; 4 bölgeli klavyelerde bölge renkleri) | ✓ | hidraw, rapor 0x5D (output report) |
| CPU sıcaklığını, GPU modunu ya da pili izleyen klavye rengi | ✓ | hidraw, rengi servis yazar |
| Klavye parlaklığı | ✓ | `asus::kbd_backlight` |
| Şarj limiti | ✓ | `charge_control_end_threshold` |
| Fn+F5 | ✓ | servis `platform_profile`'ı izler, modun ayarlarını yeniden uygular |
| Uykudan dönüşte yeniden uygulama | ✓ | systemd-sleep kancası |

Henüz yok: AniMe/Slash matrix, M4/ROG tuşu ataması (KDE kısayoluna `boreasctl mode next`
bağlanabilir), GPU saat ofsetleri, Ryzen undervolt (Curve Optimizer), çevre birimleri (fare, kulaklık),
fan histerezisi, klavye ışığının uyku/kapanış ayarları (Aura power), prize/pile göre ayrı
klavye parlaklığı, tek seferlik %100 şarj, açılış sesi (`boot_sound`), USB-C şarj modu,
tuş başına renk.

## Kurulum

Gereksinimler: systemd kullanan bir dağıtım, asus-wmi / asus-armoury sürücüleri olan bir çekirdek,
Python 3.11+, Qt SVG ile birlikte PySide6 (arayüz için), KDE'de ekran yenileme hızı için `kscreen-doctor` ve Wayland'de keskin
görüntü için Qt'nin Wayland eklentisi. Python ve PySide6'yı dağıtımınızın paket yöneticisiyle kurun
(Arch / CachyOS'ta: `sudo pacman -S --needed python pyside6 qt6-svg qt6-wayland`). Arayüzü ve `boreasctl`'i
kullanmak için kullanıcınız `wheel` grubunda olmalı: servis soketi yalnız root'a ve bu gruba açıktır.
`wheel` grubu olmayan dağıtımlarda (Debian, Ubuntu) önce grubun oluşturulması gerekir; kurulum betiği iki
durumda da uyarır.

```bash
git clone https://github.com/Yusuf-Yonturk/boreas.git
cd boreas
sudo ./install.sh
```

İsteğe bağlı eBPF GPU yardımcısı (`boreas-gpu-engine`) depoda tutulmaz: CI onu Rust kaynağından
derleyip her sürümün GitHub Release'ine ekler; kurulum betiği kurulan sürüm için indirir ve hiçbir
şey yazmadan önce sha256 sağlama toplamını denetler. İndirme başarısız olursa Boreas yeni bir yardımcı
olmadan kurulur (kurulu bir yardımcı varsa korunur) ve yalnız eBPF arka ucu kullanılamayabilir. Çevrimdışı kurmak ya da kendi derlemenizi kurmak
için dosyayı gösterin: `sudo BOREAS_GPU_ENGINE=/yol/boreas-gpu-engine ./install.sh`.
Bkz. [docs/gpu-engine.md](docs/gpu-engine.md).

Kurulum arayüz dilini sorar (Enter önceki seçimi korur, ilk kurulumda English). Sormadan kurmak için: `sudo BOREAS_LANG=tr ./install.sh`.
Her kullanıcı dili sonradan penceredeki küre düğmesinden değiştirebilir. Çince ve Japonca için
`noto-fonts-cjk`, Hintçe ve Arapça için `noto-fonts` gerekir.

### Arch / CachyOS: paket olarak kurmak

Depoda PKGBUILD'ler var (`packaging/aur/`); böylece dosyalar pacman'e ait olur ve pacman onları geri
kaldırabilir. `boreas-git`, `main` dalındaki son kodu derler:

```bash
git clone https://github.com/Yusuf-Yonturk/boreas.git
cd boreas/packaging/aur/boreas-git
makepkg -si
sudo boreas-setup enable
```

Paket yalnız dosyaları yerine koyar; `sudo boreas-setup enable` dizüstünü Boreas'a devreder (kurulum
betiğiyle aynı servis değişiklikleri, aşağıda listeli), `sudo boreas-setup disable` geri verir.
`disable` sonrasında Boreas, `sudo boreas-setup enable` çalıştırılana kadar güncellemelerden sonra da kapalı kalır.
`sudo pacman -R boreas-git` bu geri verme adımını kendisi çalıştırır. Pakette uygulama içi güncelleme
ve eBPF GPU yardımcısı yoktur. Güncellemek için depoda `git pull`, aynı klasörde yeniden `makepkg -si`.

### Kurulum sisteminizde neyi değiştirir

- `asusd`, `asus-shutdown`, `supergfxd` ve `power-profiles-daemon`'ı maskeler, `boreasd`'yi açar.
  Hepsi aynı sysfs dosyalarına yazıyor; asusd udev kuralıyla, power-profiles-daemon masaüstü tarafından
  D-Bus ile her açılışta başlatıldığı için `disable` yetmez.
- Masaüstünün kendi güç profili menüsü (KDE pil simgesi, GNOME hızlı ayarlar) bu yüzden boş görünür;
  modu Boreas yönetir. Kontrolü geçici olarak geri vermek için:
  `sudo systemctl stop boreasd && sudo systemctl unmask power-profiles-daemon && sudo systemctl start power-profiles-daemon`.
  Kontrolü Boreas'a geri vermek için (ikisi birlikte çalışmamalı, maske kendiliğinden geri gelmez):
  `sudo systemctl mask --now power-profiles-daemon && sudo systemctl start boreasd`.
- İlk açılışta servis mevcut şarj limitini, klavye parlaklığını, panel overdrive'ı, modu ve GPU modunu
  devralır. Başka bir aracın kurduğu özel fan eğrileri ve güç limitleri devralınmaz: mod, firmware fan
  eğrileri ve güç limitleriyle ve varsayılan CPU Boost politikasıyla uygulanır.
- `boreasd` root olarak çalışır ve yerel bir soketi dinler (`/run/boreas.sock`, `wheel` grubu). Servis
  ağa bağlanmaz. Tepsi uygulaması GitHub'a son sürümü sorar (kapatılabilir, bkz.
  [Güncelleme](#güncelleme)), kurulum betiği de GPU yardımcısını bu deponun GitHub Release'inden indirir.
- `sudo ./uninstall.sh` kurulumun eklediği maskeleri kaldırır ve o servisleri geri açar. Boreas'ı kurmadan
  önce kendi maskelediğiniz ya da kapattığınız bir servis olduğu gibi kalır. (İlk kurulumu Boreas 0.1.7 ya
  da daha eskisiyle yapıldıysa, kendi maskelediğiniz bir servis Boreas'ınki sayılır ve geri gelir.)

Güç profilini masaüstüne geri verdiyseniz (yukarıda), KDE'nin prize ve pile ayrı güç profillerini
(KDE'de Ayarlar > Güç Yönetimi) ya da Boreas'ın "pil/prize göre otomatik mod"unu kapatın.

## Kullanım

```bash
boreas                 # arayüz (tepsi)
boreasctl status
boreasctl mode turbo   # sessiz | dengeli (silent | balanced) | turbo | next | prev | <id> (özel modlar; kimlikler `boreasctl status`'ta)
boreasctl gpu eco      # eco | standard | optimized | ultimate
boreasctl charge 80
boreasctl cputemp 85   # etkin mod; default = firmware varsayılanı
boreasctl cpufloor 45  # hedef SPL'yi 45 W altına çekmez; min = firmware minimumu
```

Arayüz sistemin açık/koyu temasını izler; başlıktaki düğme otomatik, açık ve koyu arasında geçer.
Başlıktaki düğme Otomatik'teyken `BOREAS_THEME=light boreas` ya da `dark` temayı seçer. `BOREAS_SCALE=1` arayüz boyutunu değiştirir
(varsayılan `0.78`), `BOREAS_LANG=tr boreas` dili zorlar. Sağ üstteki **Fanlar ve güç** düğmesi aynı pencerede fan eğrileri,
güç limitleri ve pil sayfasını açar; tekrar basınca ana sayfaya döner.

## Güncelleme

Boreas açıldıktan 10 saniye sonra, ardından günde bir kez GitHub'a bakar; her seferinde yalnızca yayımlanmış
son sürümü sorar. Yeni bir sürüm varsa pencerede **Güncelle · Sürüm notları ·
Gizle** görünür. **Güncelle** önce yönetici şifrenizi sorar (pkexec), ardından Boreas ile kurulan bir yardımcı o
sürümün kaynağını GitHub'dan root olarak indirip kurar ve uygulama yeniden başlar. **Sürüm notları** [CHANGELOG.md](CHANGELOG.md) dosyasını açar. Yardımcı
yalnız kurulu olandan yeni bir sürümü kurar. 0.1.10'dan itibaren GitHub'daki sürümler değiştirilemez: bir
sürüm yayımlandıktan sonra kaynağı ve ona eklenen GPU yardımcısı değiştirilemez.

Elle güncellemek için:

```bash
cd boreas
git pull
sudo ./install.sh
```

Ardından pencerede **Çıkış**'a basıp `boreas` ile yeniden açın. Kontrol tepsi menüsünden
(**Güncellemeleri denetle**) ya da `BOREAS_NO_UPDATE_CHECK=1` ile kapatılabilir.

## CPU sıcaklık hedefi

Her moda 75-95 °C arasında bir hedef verilebilir. Hedefin üstünde Boreas, ASUS'un CPU güç limitlerini
`asus-armoury` üzerinden düşürür, CPU soğuyunca yeniden yükseltir; modun ve sizin limitlerinizin
belirlediği değerin üstüne hiç çıkmaz. Limitler tabana inip CPU hâlâ sıcaksa modun CPU politikasını
kademe kademe düşürür (EPP, sonra boost kapatma; Performans güç profilinde önce boost kapatma, sonra güç
profili, sonra EPP), soğuyunca ters sırayla geri alır. İsteğe bağlı, mod başına bir **sürekli güç tabanı**
sayesinde bir oyun modu firmware'in minimumu yerine (G713PV'de 30 W) örneğin 45 W'ta tutulabilir. Taban
yalnız yazılımsal hedefte geçerlidir.

```bash
boreasctl cputemp 85        # etkin modun hedefi
boreasctl cputemp default   # kapat
boreasctl cpufloor 45       # sürekli güç tabanı; `cpufloor min` = firmware minimumu
```

**CPU'nun kendi tavanı (aşağıda) kullanılmıyorsa Boreas bu hedefi yazılımla tutar, bu yüzden CPU bir süre
hedefin üstüne çıkabilir.** Boost, firmware,
GPU ile paylaşılan soğutma ve servis gecikmeleri bu aşımlara yol açabilir; bu yazılımsal hedef yalnız dizüstünün
aralığı bildirilen ASUS CPU güç kontrollerini sunduğu yerde kullanılabilir; CPU'nun kendi tavanı (aşağıda)
bunlara ihtiyaç duymaz. Başka bir güç ayarlama servisiyle birlikte çalıştırmayın.
Denetleyicinin adımları, hata durumunda ne yaptığı ve durum alanları
[docs/cpu-temperature.md](docs/cpu-temperature.md) dosyasında.

**CPU'nun kendi tavanı (Ryzen, varsayılan açık, henüz yeni).** Birçok AMD Ryzen dizüstünde hedef doğrudan
CPU'ya verilebilir; CPU onu milisaniyeler içinde kendisi tutar. Bunun için DKMS ile
derlenen küçük bir çekirdek modülü gerekir. Kurulum bunu aşağıda listelenen her Ryzen ailesinde
kendiliğinden açar; modülün derlenebilmesi için `dkms` ve çekirdeğinizin başlık dosyaları kurulu olmalı.
Komut AMD'nin kendisine ait (SMU'nun sıcaklık sınırı mesajı); RyzenAdj ve G-Helper da aynısını gönderir,
yani iyi denenmiş bir komuttur. Kapatma tercihi
`/etc/boreas/smu-off` dosyasına kaydedilir; sonraki kurulumlar ve güncellemeler kapalı bırakır:

```bash
sudo BOREAS_SMU=0 ./install.sh # kapatır; sonraki kurulumlarda ve güncellemelerde kapalı kalır
sudo BOREAS_SMU=1 ./install.sh # yeniden açar
```

Boreas'ı paket olarak mı kurdunuz (bkz. yukarı)? O zaman modül ayrı bir pakettir;
`sudo pacman -R boreas-smu-dkms` ile kaldırılır:

```bash
cd boreas/packaging/aur/boreas-smu-dkms
makepkg -si
sudo modprobe boreas_smu && sudo systemctl try-restart boreasd   # ya da yeniden başlatın
```

Boreas CPU ailesini kendisi tanır (Ryzen 2000-5000 mobil ve G serisi APU'lar, 6000-8000 mobil,
7045HX ve 9000HX serileri, Ryzen AI 300 / AI Max; grafik birimi olmayan masaüstü Ryzen 3000 / 5000 hariç). Modül başka bir CPU'da ve BIOS ile paylaştığı kilidi (`\_SB.AM00`) sunmayan bir
BIOS'ta yüklenmez. Modül yoksa, yüklenmezse ya da hata bildirirse Boreas yukarıdaki yazılımsal hedefle
devam eder. Ayrıntılar ve aile listesi [docs/cpu-smu.md](docs/cpu-smu.md) dosyasında.

Dizüstünüzün bunu kullanıp kullanamayacağını hiçbir şey kurmadan 10 saniyede görmek için (CPU ailesi ve
BIOS kilidi):

```bash
curl -LO https://raw.githubusercontent.com/Yusuf-Yonturk/boreas/main/tools/smu-check.sh
sudo sh smu-check.sh
```

## GPU modları

- **Ultimate** <-> diğerleri MUX'u değiştirir, yeniden başlatma gerekir.
- **Eco --> Standart** yeniden başlatmadan hemen geçer.
- **Standart --> Eco**: dGPU sürücüsü bağlıyken `dgpu_disable` yazılırsa sistem kilitlenebilir. Servis
  önce nvidia-persistenced ve nvidia-powerd'yi durdurur, NVIDIA modüllerini kaldırır ve PCI aygıtını
  çıkarır. dGPU kullanımdaysa (KWin, oyun, CUDA) Eco ertelenir: servis 15 saniyede bir yeniden dener ve
  açılışta ekran yöneticisinden önce uygular. KDE Plasma'da (Wayland) KWin dGPU'yu oturum boyunca açık
  tuttuğu için Eco orada bir sonraki açılışta uygulanır. NVIDIA modülü kaldırılamazsa ya da Eco başka bir
  nedenle başarısız olursa, Eco'yu yeniden seçene ya da yeniden başlatana kadar tekrar denemez.
- **Eco ve Optimized için NVIDIA dGPU gerekir.** Yukarıdaki adımlar yalnızca NVIDIA dGPU'yu bırakmayı
  bilir; bu yüzden Radeon dGPU'lu bir dizüstünde (örneğin 2022 ROG Zephyrus G14) Eco ve Optimized
  gösterilmez ve servis onları reddeder. Standart ve Ultimate her zamanki gibi çalışır. Kayıtlı bir Eco
  ya da Optimized modu bir bildirimle Standart'a döner.

## Deneysel eBPF GPU motoru

Cardwire kodundan uyarlanmış isteğe bağlı bir arka uç: ayrık GPU'yu (dGPU) kaldırmak yerine ona erişimi
engeller, böylece Eco yeniden başlatma istemez. Eco gibi bu da NVIDIA dGPU ister. Yardımcı programı kurulum betiği yerleştirir
([Kurulum](#kurulum) bölümüne bakın); **GPU modu** kartındaki **eBPF motoru (yeniden başlatmadan Eco) ·
Deneysel** anahtarıyla ya da şu komutla açana kadar kapalı kalır:

```bash
boreasctl gpu-backend ebpf       # eBPF GPU engellemesini aç
boreasctl gpu-backend firmware   # varsayılan ASUS firmware arka ucuna dön
```

Erişimi engellemek GPU'nun uyuduğunu göstermez ve bu bir güç tasarrufu iddiası değildir: G713PV'de GPU
bir çekirdekle D3cold'a indi, bir başkasıyla uyanık kaldı. Derleme, yamalı yardımcıyı güncellemelerde
koruma, sınırlar ve test kaydı [docs/gpu-engine.md](docs/gpu-engine.md) dosyasında.

## Geliştirme

```bash
python3 -m unittest discover -s tests   # sahte sysfs ağacıyla, root gerekmez
python3 -m boreas.gui.app               # arayüz (servis çalışıyor olmalı)
```

Ayarlar: `/etc/boreas/config.json`. Soket: `/run/boreas.sock` (`wheel` grubu).

Yeni sürüm yayımlamak için `boreas/__init__.py` içindeki `__version__` değerini artırın, [CHANGELOG.md](CHANGELOG.md)
dosyasına bir bölüm ekleyin ve `main`'e birleştirin. Ardından `v<sürüm>` etiketini gönderin ve hemen
sürümü kendi hesabınızla taslak olarak oluşturun:
`gh release create v<sürüm> --verify-tag --draft --title "Boreas <sürüm>"`. Sürüm işi çalıştığında bir sürüm
yoksa CI taslağı kendisi oluşturur. CI notları CHANGELOG.md'den doldurur, kurulum betiğinin indirdiği GPU
yardımcısını ekler ve sürümü yayımlar. Etiket `__version__` ile uyuşmazsa ya da CHANGELOG.md'de o sürümün
bölümü yoksa iş başarısız olur. Yayımlanan sürümler değiştirilemez. Kurulu kopyalar bir gün içinde
güncelleme notunu gösterir.

## Katkıda bulunma

Katkıda bulunmaktan çekinmeyin! Hata bildirimleri, çeviriler ve pull request'ler memnuniyetle
karşılanır; özellikle ROG Strix G713PV dışında bir ASUS dizüstünüz varsa. Çalışma adımları, testler ve
yeni dil ekleme için [CONTRIBUTING.tr.md](CONTRIBUTING.tr.md) dosyasına bakın.

## Teşekkürler

- Deneysel Rust motorunda kullanılan GPL-3.0 GPU erişim kontrolü kodu için luytan ve katkıda
  bulunanların geliştirdiği [Cardwire](https://github.com/OpenGamingCollective/cardwire) projesine.
  Bkz. [üçüncü taraf bildirimleri](THIRD_PARTY_NOTICES.md).
- ASUS donanımı üzerine yaptıkları açık kaynak çalışma için Serge'ye ve
  [G-Helper](https://github.com/seerge/g-helper) topluluğuna.
- Linux çekirdek sürücüleri (asus-wmi, asus-armoury) ve asusctl üzerindeki çalışmaları için
  [asus-linux.org](https://asus-linux.org) topluluğuna.
- AMD SMU araştırmaları için [RyzenAdj](https://github.com/FlyGoat/RyzenAdj),
  [PawnIO.Modules](https://github.com/namazso/PawnIO.Modules) ve [ryzen_smu](https://github.com/amkillam/ryzen_smu)
  projelerine.
- Herkese açık ACPI tabloları için [Linux Hardware Project](https://github.com/linuxhw/ACPI)'e.

## Lisans

Copyright (C) 2026 Yusuf Yontürk. [GPL-3.0](LICENSE) lisansıyla dağıtılır; `smu/` içindeki çekirdek modülü
GPL-2.0-only lisanslıdır. Depoya alınan Cardwire parçaları GPL-3.0 lisanslarını korur (bkz.
[üçüncü taraf bildirimleri](THIRD_PARTY_NOTICES.md)).

ASUS, ROG, TUF ve Armoury Crate, ASUSTeK Computer Inc.'in ticari markalarıdır; bu proje ASUS ile bağlantılı değildir.
