# Fiş Takip ve Z Raporu

Flask, SQLite ve OpenPyXL tabanlı fiş/Z raporu takip uygulaması.

## Kurulum

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-ocr.txt
py app.py
```

Tarayıcıdan `http://127.0.0.1:5000` adresini açın. Fotoğraf OCR'ı öncelikle PaddleOCR, kurulum/uyumluluk sorunu varsa otomatik olarak EasyOCR ile çalışır. Windows PowerShell'de bağımlılıkları kurup uygulamayı başlatın:

```powershell
pip install -r requirements.txt
py app.py
```

OCR ilk kullanımda model dosyalarını indirir. İnternet bağlantısı yoksa uygulama anlaşılır bir uyarı gösterir; manuel giriş ve Excel aktarımı çalışmaya devam eder. Vercel deployment'ında function boyutunu aşmamak için ağır OCR paketleri yüklenmez; OCR kullanımı için uygulamayı yerel olarak `requirements-ocr.txt` ile çalıştırın veya harici bir OCR API bağlayın.

Excel aktarımı; Fişler, Z Raporları, Aylık Özet, KDV Özeti ve Ödeme Özeti sayfalarını üretir.

## Firebase giriş ayarı

1. Firebase Console'da Authentication > Sign-in method bölümünden Email/Password'u etkinleştirin.
2. Project settings > Your apps bölümündeki web yapılandırma değerlerini kök `firebase-config.js` dosyasına yazın.
3. Her kullanıcıyı Firebase Authentication > Users bölümünden şu e-posta biçimiyle oluşturun: `TCNO@celikel-smm.local`.
4. Kullanıcı giriş ekranında 11 haneli TC numarasını ve Firebase şifresini girer; başarılı girişten sonra `/app` açılır.

TC kimlik numarası parola olarak kullanılmaz. Firebase yalnızca bu numaradan türetilen kullanıcı e-postasını hesap anahtarı olarak kullanır.