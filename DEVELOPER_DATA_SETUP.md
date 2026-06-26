# Yuktra-EQ — Data folder setup (developer ke liye)

Installer **bina data ke** banta hai (`.exe` me data nahi hota). Data **bahar se**
diya jata hai. Yahan **DO machine** alag-alag hain — paths bhi alag:

- 🐧 **VM (Linux)** — yaha `C:\` / `D:\` **NAHI** hota. Ingestion data + zip yahan rehti hai. Developer ka packaging kaam yahan.
- 🪟 **Windows PC** — yaha app **install + run** hoti hai. Sirf yahaँ `C:\` paths hote hain.

---

## 🐧 PART 1 — VM (Linux): package me data daalna

Deploy base: `/home/azureuser/yuktra-ima-deploy`

```
/home/azureuser/yuktra-ima-deploy/data/            <- ingestion data (doc-management)
/home/azureuser/yuktra-ima-deploy/data/Ingested/   <- vector stores
/home/azureuser/yuktra-ima-deploy/data/models/     <- gguf models
/home/azureuser/yuktra-ima-deploy/data/emor/       <- pipeline yaha .exe zip rakhti hai
```

**Steps (VM pe, Linux commands):**
```bash
cd /home/azureuser/yuktra-ima-deploy/data/emor
unzip -o Yuktra-EQ-Setup.zip -d Yuktra-EQ

# setup.exe ke BAGAL data\ folder banao
mkdir -p Yuktra-EQ/data
cp -r /home/azureuser/yuktra-ima-deploy/data/Ingested  Yuktra-EQ/data/
cp -r /home/azureuser/yuktra-ima-deploy/data/models    Yuktra-EQ/data/

# app + data ek saath zip karo (yahi download hoga)
zip -r Yuktra-EQ-Final.zip Yuktra-EQ
```

Final package:
```
Yuktra-EQ/
   yuktra-eq-setup.exe
   data/
      Ingested/<tenant>/document_text/{config.json, index.faiss, metadata.json}
      models/{embeddinggemma-300M-Q8_0.gguf, gemma-3-4b-it-Q4_K_M.gguf}
```

⚠️ **VM pe structure verify karo:**
```bash
ls /home/azureuser/yuktra-ima-deploy/data/Ingested/
ls /home/azureuser/yuktra-ima-deploy/data/Ingested/*/    # 'document_text' dikhna chahiye
ls /home/azureuser/yuktra-ima-deploy/data/models/        # 2 gguf files
```
Agar tenants `Ingested` ke bahar hain to unhe `Ingested/` ke andar laana padega.

---

## 🪟 PART 2 — Windows PC (jaha app chalti hai): runtime

User `Yuktra-EQ-Final.zip` download kare → extract → `yuktra-eq-setup.exe` **run** kare:
- App Program Files me install hoti hai, desktop icon banta hai, service chalu hota hai.
- `setup.exe` ke saath jo `data\` tha, wo install ke time yahan copy ho jata hai:
  ```
  C:\ProgramData\Yuktra-EQ\data\
  ```
- App isi `C:\ProgramData\Yuktra-EQ\data` se data padhti hai → QnA chale. ✅

### App data ko kaise dhoondhti hai (env vars — Windows pe)
| Env var | Default (installer set karta hai) | Kya |
|---|---|---|
| `DATA_DIR` | `C:\ProgramData\Yuktra-EQ\data` | poora data base |
| `YUKTRA_INGESTED_DIR` | `<DATA_DIR>\Ingested` | sirf ingested stores |
| `EMBEDDING_MODEL_PATH` | `<DATA_DIR>\models\embeddinggemma-300M-Q8_0.gguf` | embedding model |

> Developer ko code change karne ki zaroorat nahi — code already `DATA_DIR` /
> `YUKTRA_INGESTED_DIR` padhta hai. Bas data sahi jagah ho **ya** env var set ho.

### Windows pe data dene ke 3 tareeke
- **A (auto):** `data\` ko `setup.exe` ke saath rakho (PART 1) → install khud `C:\ProgramData\Yuktra-EQ\data` me daal dega.
- **B (manual):** install ke baad data ko `C:\ProgramData\Yuktra-EQ\data` me copy karo → `Restart-Service YuktraEQBackend`.
- **C (external):** data kahin bhi rakho aur env point karo:
  ```powershell
  nssm set YuktraEQBackend AppEnvironmentExtra "DATA_DIR=D:\YuktraData"
  Restart-Service YuktraEQBackend
  ```

---

## Common notes
- **Model filenames exact** hone chahiye (`embeddinggemma-300M-Q8_0.gguf`, `gemma-3-4b-it-Q4_K_M.gguf`), warna `EMBEDDING_MODEL_PATH` env se override.
- **Auto-reload:** `Ingested\` me content add/replace/delete karo → agli query pe khud reload (restart nahi chahiye). Sirf env path badlo to restart chahiye.
- **Windows pe verify:** `nssm get YuktraEQBackend AppEnvironmentExtra` → jo `DATA_DIR=...` dikhe wahi asli jagah.

### Ek line summary
> Build me data nahi hota. VM (Linux) pe developer `data\` (Ingested + models) ko
> `setup.exe` ke saath zip kar deta hai. Windows pe install karte hi wo data
> `C:\ProgramData\Yuktra-EQ\data` me chala jata hai aur QnA kaam karta hai.
