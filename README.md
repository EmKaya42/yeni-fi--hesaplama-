# Çelikel SMM · Fiş ve Z raporu çalışma alanı

Flask, SQLite, PaddleOCR/RapidOCR ve OpenPyXL tabanlı belge işleme uygulaması. Varsayılan OCR ücretsizdir ve tamamen sunucuda çalışır; belge görselleri bir bulut OCR servisine gönderilmez. Başlangıçta giriş ekranı, girişten sonra doğrudan Fişler açılır; ana sayfa, manuel belge formları ve birleşik toplu indirme bulunmaz.

## Belge akışı

- İlk kullanımda muhasebe programı seçilir; standart ana hesap kodları otomatik yüklenir. Firmanın gerçek alt hesapları için programdan dışa alınmış hesap planı bir kez yüklenir. Hesap kodu yazma alanı yoktur; kodlar ekranda salt okunur gösterilir ve Excel'e sunucu tarafından atanır. Önceki sürümde elle kaydedilen kodlar otomatik kodların yerine kullanılamaz.
- Firma hesap planı yüklenen dosyanın adıyla kaydedilir. Firma seçimi belge yüklemeden önce yapılır; belgeler, mükerrer kontrolü, kuyruk ve Excel her firma için ayrıdır. Dönem seçimi ve dosya, ürün, satıcı veya belge numarasıyla arama yapılabilir. Daha önce Excel hazırlanmış belgeler işaretlenir; bu işaret dış muhasebe programına aktarımın tamamlandığı anlamına gelmez.
- JPG, PNG, WEBP, TIFF, BMP ve PDF desteklenir. Tek seçimde dosya sayısı sınırı yoktur; 50, 100, 125 ve daha fazla dosya tek tek HTTP istekleriyle yüklenir. Her dosya en fazla 20 MB, her PDF en fazla 100 sayfadır. Her PDF sayfasında **tek belge** bulunmalıdır.
- Dosyalar yüklenirken sekme açık kalmalıdır. Sunucuya ulaşmış belgeler, sekme kapansa bile kalıcı SQLite kuyruğundan sırayla okunur. İşletim sistemi/kapasite kaynaklı sınırlar hâlâ geçerlidir.
- Durumlar: sırada, okunuyor, kontrolleri geçti (tik), inceleme gerekli, hesap eşleşmesi gerekli, okunamadı, mükerrer. Sonuç, ödeme dağılımı, oluşturulacak muhasebe fişi ve kaynak belge birlikte incelenebilir. İnceleme/başarısızlık durumunda aynı dosya yeniden okunabilir; daha net bir fotoğraf ayrıca yüklenebilir.
- OCR sonucu incelemeye düştüğünde bir kez alternatif görüntü ölçeği otomatik denenir. Geçici OCR zaman aşımı/işlem hataları 5 ve 10 saniye beklemeyle en fazla iki kez otomatik denenir. Sonuç yine belirsizse incelemede kalır; kullanıcı ayrıca Yeniden dene kullanabilir. Deneme sayıları ve bekleyen işler veritabanında saklanır.
- Yeniden denemede sayfa yönü kontrolü ve alternatif metin yerleşimi kullanılır. Dosya hash'i aynı firmaya aynı yüklemeyi tekrar eklemez; doğrulanmış vergi kimliği + belge no + tarih + tutar eşleşmesi farklı fotoğraftaki mükerreri dışarıda bırakır.
- Fiş Excel yalnız Fişler'de, Z raporu Excel yalnız Z Raporları'nda görünür. Seçili firmanın ilgili belge kuyruğu bitmeden çıktı alınmaz; yalnız kontrolleri geçmiş belgeler dahil edilir. İnceleme/başarısız/mükerrer belgeler dışarıda kalır ve sayıları ekranda görünür. Seçili dönemde hesap eşleşmesi eksik belge varsa aktarım durur. Excel hazırlanma tarihi, belge kimlikleri ve dosya özeti kaydedilir.
- Önceki sürümün receipts/z_reports tabloları silinmez. Aynı kullanıcıya ait eski kayıtlar ayrı arşivde görünür. Önceki sürüm tutar hataları sebebiyle eski kayıtlar doğrulanmış yeni çıktıların içine otomatik karıştırılmaz.

## Okuma doğruluğunun sınırları

Özgün renkli görüntü ve kontrastı düzenlenmiş görüntü üzerinde iki PaddleOCR okuması karşılaştırılır. Firma, ürün satırları, belge tipi/serisi, tarih, vergi kimliği, belge no, toplam, KDV kırılımı ve ödeme bilgileri eşleşmeli; zorunlu alanlar, KDV oran/matrah/tutarı ve matrah + KDV = toplam kontrolleri geçmelidir. Fişte ürün adları tutarlı satırlardan alınır; ürün toplamı fiş toplamına eşit olmalıdır. Satırların KDV oranları okunmuşsa oran bazında toplamlar da karşılaştırılır. Z raporunda ödeme toplamı ayrıca kontrol edilir. Eksik tutar sıfır yapılmaz; Decimal ile kuruş hassasiyeti korunur.

Firma adı `seller_name`, ürün adı ve tutarları `items` alanlarında ayrı saklanır. Fişin Açıklama sütununda ürün adları `; ` ile birleştirilir; aynı ad bir kez gösterilir, tüm ürün tutarları kontrol edilir. Türkçe karakterler ve ambalaj bilgileri korunur. İsimsiz, eksik tutarlı, toplamı uyuşmayan veya iade içeren ürün satırları incelemeye ayrılır. Tek KDV oranlı indirimli fişte ürün toplamı − belgede yazan indirim = genel toplam doğrulanır; indirim muhasebe tutarından tekrar düşülmez. Çok oranlı indirim, oran bazında güvenli dağıtım yapılamıyorsa incelemede kalır. Önceden `product_name` alanına firma yazılmış başarılı fişler açılışta incelemeye alınır; kaynakları ve eski sonuçları korunur, Yeniden dene ile yeni kurallarla okunur.

### Otomatik belge alanları

Fişlerde işletme unvanı, vergi dairesi, VKN/TCKN, tarih ve saat, belge numarası, ürün/hizmetin tam adı, kalem tutarı, kalemin KDV oranı, oran bazında KDV matrah/tutarı ve genel toplam okunur. Miktar × birim fiyat satırı varsa bu değerler ayrıca saklanır ve kalem tutarıyla çarpım kontrolü yapılır. Ürün ambalajındaki `1,5 L` gibi değerler satış miktarı diye doldurulmaz. Belgede bulunmayan miktar/birim fiyat boş kalır.

Z raporunda bunlara cihaz/mali sicil numarası, fiş/işlem adedi, nakit, kredi kartı, banka kartı, yemek kartı ve diğer tahsilatlar eklenir. Raporda basılıysa iptal/iade/indirim tutar ve adetleri ile kümülatif satış/KDV toplamları saklanır. Kümülatif değerler günlük satışlara eklenmez; iptal/iade toplamları net satıştan ikinci kez düşülmez. Günlük satış, KDV ve ödeme dağılımı birbirini doğrulamalıdır. Yemek kartı toplamı ve kuruluş bazında ayrıntı aynı rapordaysa tutarlar iki kez sayılmaz.

Vergi dairesi ve saat zorunludur; Z raporunda cihaz veya mali sicil numarasından en az biri ve işlem adedi de gereklidir. Eksik saat `00:00` yapılmaz. İsteğe bağlı alanın belgede bulunmaması ile açıkça `0,00` yazması farklı saklanır. Alanın etiketi basılmış ancak tutarı okunamamışsa inceleme gerekir. Yeni alanların tamamı iki OCR okuması arasında karşılaştırılır; uyuşmazlık hazır tikini engeller. Bu doğrulama VKN/TCKN'nin resmî bir sicilden doğrulandığı anlamına gelmez.

Okuma sürümü 2–5 olan kayıtlar, yeni OCR motoruyla sürüm 6 için bir kez otomatik kuyruğa alınır. Dosya, önceki okuma tamamlanana kadar eski sonuç ve Excel hazırlama geçmişi korunur. Yeni okuma inceleme gerektiriyorsa eski başarılı sonuç geri yüklenmez; belge incelemede kalır. Okuma başarısız olsa da her açılışta yeniden kuyruğa girmez. Mükerrer kimliğine mali sicil/cihaz numarası da katılır; farklı kasaların aynı günlük Z numarası ayrı tutulur. Firebase Authentication ve Firebase kullanıcı kimliğine göre erişim ayrımı korunmuştur; dosya ve sonuçlar mevcut Railway Volume/SQLite yapısında saklanır.

### Sürüm 6: ücretsiz yerel sinir ağı OCR

- Varsayılan `OCR_ENGINE=paddle`: PP-OCRv5 mobil metin tespiti, yön sınıflandırması ve Türkçe destekli PP-OCRv6 small metin tanıma. ONNX Runtime CPU üzerinde çalışır; GPU veya API anahtarı gerekmez. RapidOCR `3.9.2`, ONNX Runtime `1.30.0` ve model SHA-256 özetleri sabitlenmiştir.
- Büyük modeli otomatik olarak seçmek yerine gerçek Z raporunda süre ve alan doğruluğu karşılaştırılmıştır. Model dosyaları yaklaşık 25 MB'tır. Bu bilgisayarda uzun örnek Z raporunun tek okuması yaklaşık 9 saniye sürmüştür; sunucu ve görsele göre süre değişir.
- `python -m scripts.setup_paddle_ocr` yalnız model ağırlıklarını indirir. Docker derlemesinde çalışır. Belge okuma sırasında model indirilmez; eksik/bozuk model anlaşılır hata verir. ONNX telemetrisi kapalıdır.
- Her belge ayrı, en fazla 240 saniye çalışan OCR işleminde okunur; işlem bitince model belleği serbest bırakılır. Eğik satırlardaki sağ tutar sütunu geometrik olarak kendi etiketiyle birleştirilir.
- İki okumada uyuşmayan firma adı, vergi kimliği, belge numarası ve cihaz kimliği boş bırakılır; ham okumalar saklanır. Fotoğrafta kesilmiş unvan veya olmayan bilgi üretilmez.
- Açıkça `V.D.` / `Vergi Dairesi` olarak etiketlenmiş adlarda harf gibi basılan `$`, `1`, `5` karakterleri ad içinde düzeltilir; sayısal sözcükler korunur. Düzeltme belge notuna yazılır ve ham metin değişmez. Adresten vergi dairesi türetilmez; VKN, tarih ve tutarlara bu düzeltme uygulanmaz.
- Eksik firma unvanı, aynı kullanıcı ve firma çalışma alanında aynı VKN / TCKN ile kontrolleri geçmiş bir belgeden otomatik tamamlanır. Hedef vergi kimliği ve kaynak unvan/vergi kimliği iki ham okumayla doğrulanır. Farklı unvanlar varsa, kaynak daha önce otomatik tamamlanmışsa veya vergi kimliği belirsizse eşleştirme yapılmaz. Kaynak belge kimliği sonuçta, dosya adı ve açıklama ekranda ve Excel'in Belge Bilgileri sayfasında saklanır. Ham OCR metni değiştirilmez; diğer eksik/tutarsız alanlar incelemeyi engellemeye devam eder.
- Kısmen okunan belgelerde eksik/çelişen alanlar ayrıntıların başında ve ilk sorun liste satırında gösterilir. Eşleşen önceki belge bulunamayan kesilmiş unvanlar için tam fotoğraf gerekir.
- Tam unvanlı belge sonradan yüklense de aynı çalışma alanında aynı vergi kimliğiyle incelemede bekleyen, unvanı eksik belgeler otomatik yeniden kuyruğa alınır. Tamamlanmış belgeler yeni eşleştirmelerin kaynağı yapılmadığından zincirleme tamamlama ve tekrar döngüsü oluşmaz.
- Eski motor yalnız açıkça `OCR_ENGINE=tesseract` seçilirse kullanılabilir. Varsayılan akış Tesseract'a geri dönmez.

[RapidOCR model belgeleri](https://rapidai.github.io/RapidOCRDocs/main/en/model_list/).

### Sürüm 5 doğruluk düzeltmeleri

- Sürüm 5'teki Tesseract `tur+eng` motoru, karşılaştırma ve geriye dönük kullanım için tutulur. Güncel varsayılan motor yukarıdaki sürüm 6 akışıdır.
- Belirli bir fişe ait sabit tarih, saat, VKN ve tutar yazan kurallar kaldırıldı. Eksik ödeme, toplamdan kredi kartına tamamlanmaz; çelişen tutarlar çoğunluk oylamasıyla seçilmez ve fazla basamaklar silinmez.
- Normal fişin altındaki `Z NO` / `EKÜ NO`, belgeyi Z raporuna çevirmez. Yanlış bölümdeki belge incelemede tutulur; kullanıcı belge türünü değiştirebilir. İşlem sürerken tür değiştirilemez.
- Z raporundaki günlük satış, KDV, departman, ödeme, belge tipleri ve iptal/iade bölümleri ayrılır. Tekrar basılmış ödeme özetleri karşılaştırılır; yemek kartı ve eşit tutarlı farklı ödemeler kaybolmaz.
- `%20.00` gibi oranlar ürün fiyatı sayılmaz. Ürünler tam okunmuş ve basılı toplam KDV ile uyuşmuşsa çok oranlı fiş dağılımı ürün tutarlarından hesaplanabilir; hesaplanan alanlar notlarda belirtilir.
- OCR blokları görseldeki satır sırasına yerleştirilir. Alternatif denemede 180 derece dahil yön düzeltmesi, yerel gölge giderme ve uyarlamalı eşikleme kullanılır. Görsel boyutu ve piksel sayısı sınırlıdır.
- İki okuma; eksik alanlar, sıfır tutarlar, ürün miktarı/fiyatı ve ödeme dağılımı dahil karşılaştırılır. Biri eksik/çelişkiliyse diğerinin az hatalı olması belgeyi otomatik başarılı yapmaz. Her iki ham okuma ayrıntı API'sinde saklanır.
- VKN, belge numarası ve cihaz/mali sicil numarası için tek tek sözcük güveni de kontrol edilir. Belge ortalaması yüksek olsa bile kritik numarada düşük güven varsa inceleme gerekir; `O` harfi tahminen `0` yapılmaz.

Tik, **otomatik kontrollerin geçmesi** anlamına gelir; gerçek dünyada %100 OCR doğruluğu veya muhasebe/mevzuat uygunluğu garantisi değildir. Aynı yanlış metin iki okumada da çıkabilir. Kaynak belge muhasebeci tarafından incelenmelidir. Düşük güvenli/çelişkili okumalar, tam ayrıştırılamayan çok oranlı KDV, dövizli belgeler ve toplamı uymayan Z raporları incelemede kalır. Tevkifat, özel matrah, indirilemeyen KDV ve istisna otomatik vergi kararı olarak uygulanmaz. Standart planda gider 770, gelir 600 kullanılır. Firma planı yüklüyse kırtasiye, akaryakıt, temizlik, yemek ve haberleşme ürünleri mevcut 770 alt hesaplarının adlarıyla eşleştirilir. Eşleştirme yalnız tek uygun hesap bulunduğunda yapılır; giderin vergi açısından indirilebilirliğine karar vermez. Birden fazla gider türü veya tanınmayan ürün, uygun genel gider hesabı yoksa inceleme gerektirir.

## Firma hesap planı ve bankalar

Muhasebe programı ayarlarından `.xls`, `.xlsx` veya `.csv` hesap planı yüklenebilir (en fazla 5 MB / 10.000 hesap). `Hesap Kodu` ve `Hesap Adı` zorunludur. Dosyada varsa `Banka Kodu` / `EFT Kodu`, `Banka Adı`, `IBAN`, `Kart Son 4 Hane`, `KDV Oranı` ve `Para Birimi` sütunları da tanınır. Banka adı ve KDV oranı hesap adından da tanınabilir. Sayfa başlıkları ilk 20 satırda aranır; kodun baştaki sıfırları ve ayırıcıları korunur. Kaynak program kodları metin olarak dışa aktarmalıdır.

TCMB ödeme sistemi katılımcılarından 71 banka/kurum kodu hazır gelir; katalog 13.09.2026 tarihinde doğrulanmıştır. EFT kodu banka kimliğidir; firmanın `102.01` gibi muhasebe hesabıyla aynı şey değildir. Gerçek banka/kart alt hesabı yalnız yüklenen firma planından seçilir. IBAN biçimi ve kontrol basamakları doğrulanır; banka adı/kodu/IBAN çelişkisi veya döviz bilgisi çelişkisi reddedilir. Otomatik muhasebeleştirme yalnız TL hesaplarıyla yapılır.

Nakit, kredi kartı, banka kartı, havale/EFT/FAST ve açık hesap tutarları ayrı okunur. Parçalı ödeme ayrı muhasebe satırları oluşturur; ödeme dağılımı belge toplamını tutmalıdır. Gider banka kartı ve havale ödemelerinde 102, kredi kartında kart hesabı; Z raporundaki POS tahsilatında 108 kullanılır. 300/309 alt hesabı ancak hesap adında kredi kartı olduğu belirtilmişse kart hesabı kabul edilir; banka kredisi karta çevrilmez. Z raporunda kart toplamı ve banka bazında ayrıntı birlikte basılmışsa eşitlik doğrulanır ve aynı tutar iki kez kaydedilmez.

Yemek kartı, banka kartı/POS toplamından ayrı tutulur. Firma planında kuruluş adıyla tanımlanmış uygun tek yemek kartı hesabı eşleştirilir; banka POS hesabıyla karıştırılmaz. Standart hesaplarda diğer karşı hesap, firma planında ise plandaki mevcut uygun alt hesap kullanılır. Eşleşme yoksa alt hesap uydurulmaz.

Fişte basılı POS bankası, kartı veren banka sayılmaz. Kart eşleşmesi kartın son dört hanesi ve varsa açıkça belirtilmiş kart bankasıyla; havale eşleşmesi giderde gönderen, gelirde alıcı IBAN'ıyla yapılır. Birden fazla uygun hesap veya çelişkili bilgi varsa hesap seçilmez. Firmanın planındaki bilgiler tamamlanıp **Seçili planı güncelle** kullanıldığında eşleştirme yeniden hesaplanır; belgeyi tekrar OCR'dan geçirmek gerekmez. Bu akış bankaya bağlanmaz, ödeme yapmaz ve banka ekstresini içe aktarmaz.

## Muhasebe programları ve Excel

Luca, Zirve, Logo, Mikro, ETA, Logo Netsis, Mikrokom GMS.NET, Orka, Datasoft, DİA, Zenom, Akınsoft ve Diğer profilleri vardır. Her seçimde Tek Düzen standart ana hesapları otomatik gelir: gider 770, gelir 600, indirilecek KDV 191, hesaplanan KDV 391, nakit 100, banka 102, kredi kartıyla ödeme 309, POS tahsilat 108, gider karşı hesabı 320, gelir karşı hesabı 120. Standart kodlar `services/accounting.py` içindeki katalogdan, firma alt hesapları yüklenen plandan alınır; önizleme ve çıktı aynı eşleştirmeyi kullanır.

**Standart kodlar ortak ana hesaplardır; firmaya özgü alt hesaplar yüklenen plandan gelir.** Üretici tarafından sertifikalanmış entegrasyon veya her mükellefin mevcut hesap planıyla doğrudan uyumluluk iddiası yoktur. `.01`, `.001` gibi şirket tarafından açılabilen alt hesaplar uydurulmaz. Programın sürüm ve Excel sütun ayarları ayrı, firmaların hesap planları ve belgeleri ayrı saklanır.

Varsayılan `.xlsx` çıktıları verilen Fiş Örneği.xls ve Z Raporu Örneği.xls dosyalarının veri başlıkları ve sütun sırasını izler. Fiş sayfasında Hesap Kodu, Hesap Adı, Belge Tipi, Belge Tarihi, B. Seri, Belge No, Açıklama, Borç Tutar, Alacak Tutar ve tekrar Hesap Adı bulunur. Z Raporu sayfasında seri sütunu bulunmaz, Belge No yerine Rapor No kullanılır. Her ikisinin borç/alacak toplamları gerçek kayıtlarından hesaplanır; örnek dosyaların kişi, hesap alt kodu, tutar ve açıklama notları kopyalanmaz. Z açıklaması okunan rapor numarasıyla oluşturulur. Seri belgede etiketli olarak okunmamışsa boş kalır.

Mevcut muhasebe aktarım sayfasının kolonları korunur. Aynı dosyanın devamına Türkçe **Belge Bilgileri**, **KDV Dağılımı**, **Ödeme Dağılımı**, **İndirim İptal İade** ve fişlerde ayrıca **Fiş Kalemleri** sayfaları eklenir. Bunlar özel şablonla indirilen Excel'de de bulunur; ilk sayfa seçilen programın eşleştirilmiş aktarım düzenidir. Belge/cihaz/vergi/banka numaraları metin, tarih ve saat Excel tarih/saat değeri, tutarlar ve miktarlar sayıdır. Eksik isteğe bağlı tutarlar boş hücre olarak kalır. Ödeme sayfasında belgedeki POS bankası ve firmanın eşleşen banka/kart hesabı ayrı sütunlardadır. Ek sayfaları kabul etmeyen bir üretici aktarımında yalnız ilk sayfa kullanılmalıdır.

İsteğe bağlı `.xls` veya `.xlsx` şablon yüklenirse tüm sayfaların ilk 20 satırındaki gerçek başlıklar tanınır. Hesap adı, belge tipi, belge tarihi, belge/rapor no, açıklama ve borç/alacak başlıkları otomatik eşleştirilir. Sütun sırası, başlık satırı, sayfa adı, tarih gösterimi ve sabit alanlar kaydedilir. Özel şablon yalnız ait olduğu fiş veya Z raporu türünde kullanılır; diğer tür kendi örnek düzeniyle hazırlanır. Özel makrolar, önceki başlık satırlarının içerikleri, üreticiye özgü XML/CSV kuralları ve sürüm kısıtları yeniden üretilmez.

Borç/alacak eşitliği her belge için kontrol edilir. Belge no, VKN/TCKN ve hesap kodları metindir; baştaki sıfırlar korunur. Tutarlar sayısaldır. Okunan metinler ve kullanıcı başlıkları Excel formülü olarak çalıştırılmaz. Programın ve sürümün kendi şablonuyla gerçek aktarım denemesi yapılmadan birebir uyumluluk iddia edilmez.

Üretici kaynakları:

- [TCMB ödeme sistemleri katılımcıları](https://www.tcmb.gov.tr/wps/wcm/connect/9fa62a85-5b6d-46c5-9b01-eb461d43723d/TCMB%2B%C3%96deme%2BSistemleri%2BKat%C4%B1l%C4%B1mc%C4%B1lar%C4%B1%2B(072025).pdf?MOD=AJPERES): banka/kurum kimlik kodları. URL eski bir tarih taşısa da belgenin içeriği 2026 katılımcılarını içerir.
- [TCMB IBAN Tebliği](https://www.tcmb.gov.tr/wps/wcm/connect/EN/TCMB%2BEN/Bottom%2BMenu/IBAN/Communique): Türkiye IBAN yapısı ve doğrulama.
- [Luca banka işlemleri](https://lucayazilim.freshdesk.com/support/solutions/articles/67000741850-luca-muhasebe-program-banka-i%25CC%2587%25C5%259Flemleri-men%25C3%25BCs%25C3%25BC): hesap planı ve eşleştirme kurallarıyla muhasebeleştirme.
- [Ticaret Bakanlığı yayınındaki Tek Düzen hesap planı](https://artvin.ticaret.gov.tr/data/642d4ed813b8768238acc3b1/c49f6df76c846dd1458ec9d4242aa3b3.pdf): standart ana hesap kodları ve adları.
- [Luca Firma Kurulum Yardımcısı](https://lucayazilim.freshdesk.com/support/solutions/articles/67000742339-luca-fi%CC%87rma-kurulum-yardimcisi): standart hesap planı ile şirketin kendi hesap planı farklı seçeneklerdir.

- [Luca Excel veri aktarımı](https://lucayazilim.freshdesk.com/support/solutions/articles/67000267580-01-02-09-excel-veri-aktar-m-): şablonun uygulamadan alınması gerekir.
- [Mikrokom GMS.NET dosyadan fiş aktarımı](https://mikrokom.com/pdfler/1-gmsnetfisaktarim.pdf): programın Excel şablonu ve fiş aktarımı.
- [ETA Excel’den muhasebe fişi aktarımı](https://cdn.eta.com.tr/pdf/eta-muhasebe-fisine-excel-den-fatura-transferi-27-05-2013.pdf): kolon tanımları üzerinden aktarım.
- [Zirve programlar arası aktarım](https://blog.zirveyazilim.net/diger-programlardan-veri-aktarimi): program ve formatlara göre farklı aktarım kapsamı.

## Railway

Depodaki `railway.toml` Dockerfile kullanır. Docker imajı Türkçe ve İngilizce dil paketleriyle Tesseract kurar. Eski Nixpacks ayarları kaldırılmıştır.

1. GitHub değişikliklerini Railway'in izlediği dala gönderin ve yeni deployment oluşturun. Bu depodaki yerel düzenleme kendi başına canlı servisi güncellemez.
2. **Mevcut veritabanı ve uploads klasörünü yedekleyin.** Var olan Volume'u koruyun; `/app/data` yoluna bağlı olmalıdır. Volume olmadan redeploy sonrası kalıcı dosya garantisi yoktur. Yeni Volume eski geçici diskteki verileri otomatik taşımaz.
3. `DATA_DIR=/app/data` kullanın (Docker varsayılanı). `FIREBASE_PROJECT_ID=html-web-uygulama` mevcut Firebase web yapılandırmasıyla aynıdır; başka proje kullanıyorsanız ikisini birlikte değiştirin.
4. Firebase Authentication'da Email/Password açık olmalı, Railway domaini Authorized domains listesinde bulunmalıdır. Mevcut `TC@celikel-smm.local` hesapları kullanılabilir. API gerçek Firebase ID token imzası, süre, issuer ve audience doğrular; `X-Firebase-UID` kabul edilmez.
5. Servisi **1 replica, 1 Gunicorn worker, 4 thread** ile çalıştırın. SQLite ve sıralı yerel OCR kuyruğu için bu dağıtım şekli gereklidir. `--preload` ve birden fazla web worker kullanmayın. Uyku/serverless modunu kapatın; arka plan kuyruğu sürekli çalışan servis gerektirir.
6. Healthcheck `/health`. Başlatma komutu Dockerfile'daki `ENTRYPOINT` / `CMD` üzerinden gelir; `PORT` değeri `gunicorn.conf.py` tarafından okunur. Railway panelindeki eski build/start override ayarlarını kaldırın. Docker varsayılanları `OCR_ENGINE=paddle` ve `OCR_MODEL_DIR=/app/models/ocr` şeklindedir. Derleme sırasında model dosyaları indirilir, SHA-256 kontrolü yapılır ve OCR motoru başlatılarak doğrulanır. Model indirmesi başarısızsa eksik motorla yayın yapılmaz.
7. Deployment kesintisi sırasında işlenen belge, 10 dakikalık kira süresinin sonunda yeniden kuyruğa alınır. Bekleyen işler Volume'da saklanır. Belgeleri kaldıran bir otomatik saklama politikası yoktur; Volume kapasitesi ve yedekleri yönetilmelidir.

[Railway Volume belgeleri](https://docs.railway.com/volumes). Bu sürümün kalıcı arka plan kuyruğu Vercel serverless işleyişine uygun değildir; hedef Railway'dir.

Yayın öncesi Docker bulunan bir makinede `docker build -t fis-takip .` ve `docker run --rm -p 8080:8080 -e PORT=8080 -v fis-takip-data:/app/data fis-takip` ile derlemeyi ve `/health` yanıtını kontrol edin. Ardından test hesabıyla bir fiş, bir Z raporu ve çok sayfalı PDF yükleyip belge ayrıntıları ile Excel çıktısını karşılaştırın. [Railway sağlık kontrolleri](https://docs.railway.com/deployments/healthchecks) yeni deployment için başarılı HTTP yanıtı bekler.

## Yerel geliştirme

Bu bilgisayarda hazırlanan Windows denemesi için `Denemeyi-Ac.cmd` dosyasına çift tıklayın. Uygulama `http://127.0.0.1:5055/app` adresinde giriş istemeden açılır. Deneme belgeleri ve ayarlar `data/deneme` altında tutulur; tekrar açıldığında korunur. Kapatmak için `Denemeyi-Kapat.cmd` kullanın. İlk açılışta “Diğer / özel şablon” ve standart hesap planı seçilir; ayarlardan değiştirilebilir.

Yerel PaddleOCR modelleri `.local-tools/paddle-models` içindedir. Başka bir Windows bilgisayarda bağımlılıklardan sonra `python -m scripts.setup_paddle_ocr` çalıştırılmalıdır. Yerel araçlar, başlatıcılar ve deneme verileri Docker imajına eklenmez; Docker model kurulumunu kendisi yapar.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m scripts.setup_paddle_ocr
.\.venv\Scripts\python.exe app.py
```

Yerel önizleme için `ALLOW_LOCAL_AUTH=1` yalnız loopback adresinde ve Railway/Vercel ortamı dışında kullanılabilir. Yerel önizleme verilerini ayırmak için `DATA_DIR` değişkenine ayrı bir klasör verin. Gerçek kullanıcı testinde bu seçeneği kaldırın.

## Testler

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
# Node.js bulunan ortamda belge ayrıntıları arasındaki geçiş testleri:
node --test tests/test_frontend.cjs
```

Testler 125 gerçek dosya yükleme isteği, sıralı kuyruk, sayfalama, sınırlı otomatik retry, deployment kesintisi, mükerrerler, PDF sayfaları, JWT doğrulaması, kullanıcı/firma ayrımı, KDV/tutar doğrulamaları, banka/IBAN/kart eşleştirmesi, POS bankası ile kart bankasının ayrımı, döviz ve hesap çelişkileri, dönem filtresi, Excel hazırlama geçmişi ve borç/alacak tutarlılığını kapsar. PaddleOCR testleri ayrıca çevrimdışı model seçimi, satır geometrisi, çelişen/eksik kimlikler ve işlem zaman aşımını denetler. Kuyruk testlerindeki OCR cevabı kontrollü test verisidir; üretim fotoğraflarındaki başarı oranını ölçmez. Gerçek OCR ayrıca bilinen doğrulukta fiş/Z fotoğraflarıyla kontrol edilmelidir.
