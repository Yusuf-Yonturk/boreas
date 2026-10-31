# Boreas'a katkıda bulunma

[English](CONTRIBUTING.md) · **Türkçe**

Yardımın için teşekkürler! Hata bildirimleri, yeni laptop modeli desteği, çeviriler ve kod katkıları
memnuniyetle karşılanır.

## Hata ya da yeni model bildirmek

Bir [issue](../../issues/new/choose) aç ve şunları ekle:

- laptop modeli (örn. `ROG Strix G713PV`), dağıtım ve çekirdek sürümü (`uname -r`)
- `boreasctl status` çıktısı
- servis kayıtları: `journalctl -u boreasd -b --no-pager | tail -n 100`

Boreas fan eğrilerini, CPU ve GPU güç limitlerini, CPU sıcaklık limitini ve GPU modunu doğrudan
donanıma yazar. Modelin henüz denenmediyse riskli bir şeyi değiştirmeden önce bunu issue'da belirt.

## Çalışma adımları

1. Depoyu fork'la ve kendi kopyanı klonla.
2. `main`'den bir branch aç: `feature/kisa-ad`, `fix/kisa-ad` ya da `i18n/dil-kodu`.
3. Değişikliğini yap ve testleri çalıştır (aşağıda).
4. `main`'e bir pull request aç. Tek bir konuya odaklı olsun ve nasıl denediğini yaz (hangi laptop,
   hangi dağıtım).

`main` korumalıdır: değişiklikler testler geçtikten sonra pull request ile birleştirilir.

## Kaynaktan çalıştırma

```bash
python3 -m unittest discover -s tests   # sahte sysfs ağacıyla; root ve ASUS laptop gerekmez
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests   # ekran olmadan
sudo ./install.sh                       # çalışma kopyanı kur
python3 -m boreas.gui.app               # arayüzü kaynaktan çalıştır (servis çalışıyor olmalı)
```

Gereksinimler: Python 3.11+ ve PySide6. Servis (`boreas/daemon.py`) yalnızca standart kütüphaneyi
kullanır; lütfen böyle kalsın.

## Kod kuralları

- Donanım erişimi `boreas/hw.py` ve diğer arka uç modüllerinden yapılır, asla arayüzden değil.
- Yeni davranış için test ekle. Testler sahte bir sysfs ağacı (`BOREAS_SYSFS_ROOT`) kullandığı için her
  yerde çalışır.
- Kullanıcının gördüğü metinler İngilizce yazılır ve `_()` ile sarılır (arayüz) ya da düz İngilizce ileti
  olarak kalır (servis hataları); aşağıdaki Çeviriler bölümüne bak.
- Önce bir issue'da konuşmadan yeni çalışma zamanı bağımlılığı ekleme.

## Çeviriler

Tüm çeviriler [`boreas/translations.py`](boreas/translations.py) içindedir. Her İngilizce kaynak metin,
`ORDER` sırasıyla çevirilerden oluşan bir listeye karşılık gelir.

- **Bir çeviriyi düzeltmek:** listedeki metni değiştir.
- **Yeni dil eklemek:** dil kodunu `ORDER`'a ekle, her satıra bir çeviri ekle, dilin kendi dilindeki
  adını [`boreas/i18n.py`](boreas/i18n.py) içindeki `LANGUAGES`'a ve `install.sh` içindeki listeye ekle.
- `{name}` gibi yer tutucuları olduğu gibi bırak.

Bir metin herhangi bir dilde eksikse ya da yer tutucu bozulmuşsa `tests/test_i18n.py` başarısız olur.

## Lisans

Katkıda bulunarak katkının projenin geri kalanı gibi [GPL-3.0](LICENSE) ile lisanslanmasını kabul
etmiş olursun.
