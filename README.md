# iHerb scraper — 2 Windows машинаар бүх каталог татах

iHerb.com бол Cloudflare-ийн "Just a moment" challenge-тэй (FragranceX-тэй ижил).
Тийм болохоор энэ скрейпер нь **жинхэнэ Chrome (Playwright + stealth)**-оор
challenge-ийг даваад, бараа бүрийн **schema.org JSON-LD**-ийг уншина.

Нийт ~50,000 бараа. **2 машинаар хувааж** татна:

| Машин | Команд | Татах |
|-------|--------|-------|
| **PC #1** | `run-shard0.bat` | тэгш ID (~25k) |
| **PC #2** | `run-shard1.bat` | сондгой ID (~25k) |

Хоёр машин хооронд холбоо хэрэггүй — тус бүр өөрийн файл руу бичнэ
(`data/products.shard0.jsonl`, `data/products.shard1.jsonl`), сүүлд нэгтгэнэ.

---

## 1. Windows бэлдэц (2 машин дээр адилхан, нэг удаа)

**a) Python + Chrome суулгах:**
```powershell
winget install Python.Python.3.12
winget install Google.Chrome
```
(Chrome заавал биш — байхгүй бол bundled Chromium ашиглана.)

**b) Энэ фолдерийг машин руу хуулах** (USB / Google Drive / GitHub).

**c) Setup скрипт ажиллуулах** (venv + сангууд + Chromium + унтахаас сэргийлэх):
```powershell
cd C:\iherb-scraper
powershell -ExecutionPolicy Bypass -File setup-windows.ps1
```

> ⚠️ **Унтахаас сэргийлэх** заавал чухал — лаптоп унтвал татаж байгаа ажил зогсоно.
> `setup-windows.ps1` үүнийг автоматаар хийнэ. Лаптоп бол нэмж: тагийг хаахад
> "Юу ч хийхгүй" болго (Control Panel → Power Options).

---

## 2. Ажиллуулах

**PC #1** дээр:
```
run-shard0.bat
```
**PC #2** дээр:
```
run-shard1.bat
```

Энэ нь `watchdog.ps1`-ийг дуудна → скрейпер унтарвал/гацвал автоматаар дахин эхэлнэ,
shard бүрэн дуустал. Ахиц `data/products.shardN.jsonl`-д шууд бичигдэх тул дахин
эхлэхэд эхнээс нь биш, тасарсан газраасаа үргэлжилнэ.

### Background-д ажиллуулах (нэвтрэхгүйгээр, ачаалахад автомат)

Task Scheduler ашиглавал хүн нэвтрээгүй ч, дахин асаахад ч үргэлжилнэ:

1. **Task Scheduler** нээ → *Create Task* (Basic биш).
2. **General**: нэр өг; ☑ *Run whether user is logged on or not*; ☑ *Run with highest privileges*.
3. **Triggers** → New → *At startup*.
4. **Actions** → New → Program: `powershell`
   Arguments (PC #1):
   ```
   -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\iherb-scraper\watchdog.ps1" -Shard 0
   ```
   (PC #2 дээр `-Shard 1`)
5. **Conditions**: ☐ *Start the task only if on AC power* (унтраа — тэгэхгүй бол батерейтэй үед зогсоно).
6. OK → одоо ажиллуулахыг хүсвэл task дээр баруун товч → Run.

---

## 3. Үр дүн нэгтгэх

2 машины файлыг нэг газар цуглуулаад (PC #2-ийнхийг `pc2/` дотор тавь гэж бодоход):
```powershell
python merge.py data\products.shard0.jsonl pc2\products.shard1.jsonl -o data\products.jsonl
```
→ `product_id`-аар давхардал арилгаж, нэг `products.jsonl` гаргана. Энэ файлыг
goodprice VPS руу хуулж import хийнэ (FragranceX-ийн адил: орчуулга → DRAFT/merge).

---

## Бичлэгийн бүтэц (JSONL мөр бүр)

```json
{
  "product_id": "124745",
  "url": "https://www.iherb.com/pr/.../124745",
  "name": "California Gold Nutrition, Vitamin D3 + K2 as MK-7, 180 Veggie Capsules",
  "brand": "California Gold Nutrition", "brand_id": "CGN",
  "category": "Supplements", "category_id": "1855",
  "sku": "CGN-02333", "mpn": "CGN-02333", "gtin": "898220023332",
  "list_price": 16.20, "display_price": 9.22, "sale_price": 9.22,
  "currency": "USD", "price_hidden": false, "availability": "InStock",
  "rating": 4.8, "review_count": 31769,
  "weight_value": "0.14", "weight_unit": "kg",
  "description": "...", "main_image": "https://...jpg", "images": ["..."],
  "specs": {"upc": "898220023332", "package_quantity": "180 count", "best_by": "01/2028", ...},
  "supplement_facts": "...", "ingredients": "...", "directions": "...", "warnings": "...",
  "ok": true, "shard": 1, "scraped_at": 1781000000
}
```

---

## Тохиргоо (хурд vs. блок)

`watchdog.ps1` доторх `-Concurrency` / `-Delay` эсвэл шууд:
```powershell
python scrape.py --shard 0 --concurrency 2 --delay 2
```
- **2 машин нэг гэрийн сүлжээнд (нэг IP)** бол `--concurrency 1-2 --delay 3` барь.
- **Өөр өөр сүлжээнд** бол `--concurrency 3 --delay 1.5` хүртэл нэмж болно.
- Хэрэв олон бараа "blocked" гарвал delay-ийг өсгө, эсвэл нэг удаа `--headed`-ээр
  ажиллуулж CF-ийг гар аргаар давуулбал cookie тогтоно.

**Туршилт** (3 бараа татаад зөв эсэхийг шалгах):
```powershell
python scrape.py --shard 0 --limit 3
```

| Команд/flag | Утга |
|---|---|
| `--shard N --shards M` | M машины N дугаарын хувь |
| `--concurrency` | зэрэг татах хуудас (default 2) |
| `--delay` | хуудас тус бүрийн завсар, сек (default 2) |
| `--recycle` | хэдэн бараа тутамд browser-ийг шинэчлэх (default 300) |
| `--rediscover` | sitemap-ийг дахин уншиж URL шинэчлэх |
| `--headed` | browser цонхыг харуулах (debug) |

Exit codes: `0` = shard бүрэн дууссан, `2` = browser асаагүй, `1` = бусад алдаа.
