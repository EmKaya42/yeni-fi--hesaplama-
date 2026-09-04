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

Render için Python sürümünü `3.11.9` yapın. PaddlePaddle Linux paketleri Python 3.11 ile kullanılabilir; Python 3.13 seçilirse uygulama otomatik olarak EasyOCR'a düşer.

## Railway kurulumu

1. Railway'de **New Project > Deploy from GitHub Repo** seçin ve bu GitHub deposunu bağlayın.
2. Runtime olarak `Python 3` seçin. Railway > Variables bölümüne `PYTHON_VERSION=3.11.9` ekleyin. Repo içinde `.python-version` ve `runtime.txt` dosyaları da bu sürümü belirtir.
3. Build command olarak `pip install -r requirements-railway.txt` yazın.
4. Start command olarak `gunicorn app:app --bind 0.0.0.0:$PORT` yazın.
5. Firebase Console > Authentication > Settings > Authorized domains bölümüne Railway domainini ekleyin.
6. SQLite veritabanının restart sonrası korunması için Railway'de Volume oluşturup `/app/data` yoluna bağlayın.

Railway deploy'unda PaddleOCR ve EasyOCR çalışır. İlk OCR kullanımında model dosyaları indirileceği için ilk istek uzun sürebilir.

Excel aktarımı; Fişler, Z Raporları, Aylık Özet, KDV Özeti ve Ödeme Özeti sayfalarını üretir.

## Firebase giriş ayarı

1. Firebase Console'da Authentication > Sign-in method bölümünden Email/Password'u etkinleştirin.
2. Project settings > Your apps bölümündeki web yapılandırma değerlerini kök `firebase-config.js` dosyasına yazın.
3. Her kullanıcıyı Firebase Authentication > Users bölümünden şu e-posta biçimiyle oluşturun: `TCNO@celikel-smm.local`.
4. Kullanıcı giriş ekranında 11 haneli TC numarasını ve Firebase şifresini girer; başarılı girişten sonra `/app` açılır.

TC kimlik numarası parola olarak kullanılmaz. Firebase yalnızca bu numaradan türetilen kullanıcı e-postasını hesap anahtarı olarak kullanır.