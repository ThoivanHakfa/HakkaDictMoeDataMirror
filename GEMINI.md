# HakkaDictMoeDataMirror

## Project Overview
This repository is a data mirror for the MOE 《臺灣客語辭典》 (HakkaDict MOE). It contains scripts to download the upstream `.ods` source files for the **Si-yen (四縣腔)** and **Nam-si-yen (南四縣腔)** dialects, process and merge them, and publish the results as CSV and JSON. It also performs phonetic reading conversions from KPPY to PFS. 

Distribution is handled via GitHub Pages under the `public/` directory, which is organized by version timestamp.

## Building and Running
The build process requires Python 3, Node.js, and JDK 17+ (for building the `KonvertToPFS` dependency).

### Initial Setup
1. Clone with submodules (if not already done):
   ```bash
   git submodule update --init
   ```
2. Build the `KonvertToPFS` JS library (required once or after submodule updates):
   ```bash
   cd lib/KonvertToPFS && ./gradlew :lib:jsNodeProductionLibraryDistribution && cd ../..
   ```
3. Setup Python virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

### Running the Build
The entire build process is executed via a single Python script.

```bash
# Download fresh ODS files and build a new version
python script/python/build_mirror.py

# Reuse the latest downloaded ODS files instead of downloading again
python script/python/build_mirror.py --reuse-ods
```

## Development Conventions
* **Terminology**: Follow the global `GEMINI.md` terminology (e.g., use Hakfa, Roman Orthography, KPPY, PFS). In English text, use **Si-yen** and **Nam-si-yen** (avoid "Sixian" or "South Sixian").
* **Output Artifacts**: Versioned outputs in `public/<version>/` are intentionally tracked in version control. Do not rewrite or delete prior version directories. If you need a new version, run a fresh build. If you must retrofit an existing version, modify it in place and document the reason.
* **File Restrictions**: The `.gitignore` file allows `*.ods` files *only* under the `public/**/` directories. Do not commit `.ods` files anywhere else.
* **Auto-generated Documentation**: The `README.md` file is partially auto-generated. The "Version ID", "Last Updated", and download paths in `README.md` are overwritten by the build script on each run. You can manually edit the surrounding prose, but hand edits to those specific fields will be lost.
* **Testing and Linting**: There is currently no formal test suite, linter, or CI configured for this project.
