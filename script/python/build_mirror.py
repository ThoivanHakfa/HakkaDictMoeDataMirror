import os
import subprocess
import datetime
import requests
import pandas as pd
import json
import re

# Upstream ODS files. Each entry: dialect-label → (upstream filename, local filename, manifest-key).
MOE_DOMAIN = "https://hakkadict.moe.edu.tw"
_UPSTREAM_DIR = "/static/resource/客語資源下載/本辭典的文字"

# Dialects that are read, merged, and converted (PFS conversion + symbol form).
PROCESSED_DIALECTS = [
    ("四縣腔",   "四縣腔詞條詞目文字.ods",   "HakkaDictMoeData_Siyen.ods",    "siyen"),
    ("南四縣腔", "南四縣腔詞條詞目文字.ods", "HakkaDictMoeData_NamSiyen.ods", "nam_siyen"),
]

# Dialects that are only mirrored as raw ODS (not merged into CSV/JSON).
EXTRA_DIALECTS = [
    ("海陸腔", "海陸腔詞條詞目文字.ods", "HakkaDictMoeData_Hailuk.ods",    "hailuk"),
    ("大埔腔", "大埔腔詞條詞目文字.ods", "HakkaDictMoeData_Taiphu.ods",    "taiphu"),
    ("饒平腔", "饒平腔詞條詞目文字.ods", "HakkaDictMoeData_Ngiauphin.ods", "ngiauphin"),
    ("詔安腔", "詔安腔詞條詞目文字.ods", "HakkaDictMoeData_Ciauon.ods",    "ciauon"),
]

ALL_DIALECTS = PROCESSED_DIALECTS + EXTRA_DIALECTS

DIALECT_LABELS = {
    "四縣腔":   "Si-yen",
    "南四縣腔": "Nam-si-yen",
    "海陸腔":   "Hai-luk",
    "大埔腔":   "Tai-phu",
    "饒平腔":   "Ngiau-phin",
    "詔安腔":   "Ciau-on",
}

# Tone digit -> symbol mapping per KPPY (教育部客家語拼音方案, 2008/2012).
# Si-yen and Nam-si-yen share the same tone categories.
TONE_DIGIT_TO_SYMBOL = {
    "四縣腔":   {"24": "ˊ", "33": "+", "11": "ˇ", "31": "ˋ", "55": "",  "2": "ˋ", "5": ""},
    "南四縣腔": {"24": "ˊ", "33": "+", "11": "ˇ", "31": "ˋ", "55": "",  "2": "ˋ", "5": ""},
    "海陸腔":   {"53": "ˋ", "55": "",  "24": "ˊ", "11": "ˇ", "33": "+", "5": "",  "2": "ˋ"},
    "大埔腔":   {"33": "+", "35": "ˊ", "113": "ˇ", "31": "^", "53": "ˋ", "21": "^", "54": "ˋ"},
    "饒平腔":   {"11": "ˇ", "55": "",  "53": "ˋ", "24": "ˊ", "2": "ˋ", "5": ""},
    "詔安腔":   {"11": "ˇ", "53": "ˋ", "31": "^", "55": "",  "24": "ˊ", "43": "ˋ"},
}

KPPY_PITCH_TO_INTERNAL = {
    "四縣腔":   {"24": "1", "11": "2", "31": "3", "55": "4", "5": "5", "2": "6"},
    "南四縣腔": {"24": "1", "11": "2", "31": "3", "55": "4", "5": "5", "2": "6"},
}

_SYL_RE = re.compile(r"([A-Za-z]+)(\d+)")

# Variant-reading markers that may prefix a segment in the upstream 音讀 column.
# Order matters: longest first so 又俗音 wins over 又讀, 特殊音 over 特, 合音讀 over 合音.
_MARKERS = ['又俗音', '又讀', '俗音', '特殊音', '合音讀', '合音', '文', '白', '特']
_MARKER_RE = re.compile(r'^(' + '|'.join(_MARKERS) + r')(?:(\d+)\.)?')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONVERT_JS = os.path.join(SCRIPT_DIR, "..", "js", "convert_to_pfs.js")


def parse_segments(reading):
    """Split a 音讀 string into [(tag, phonetic), ...].

    Segments are split on the fullwidth space (　). Each segment may start with
    a marker (with optional numeric ordering like `1.`); the marker plus its
    number form the tag (e.g. '又俗音', '文1'). Empty segments are dropped.
    """
    if not isinstance(reading, str) or not reading.strip():
        return []
    out = []
    for seg in reading.split('　'):
        seg = seg.strip()
        if not seg:
            continue
        m = _MARKER_RE.match(seg)
        if m:
            tag = m.group(1) + (m.group(2) or '')
            phonetic = seg[m.end():].strip()
        else:
            tag, phonetic = '', seg
        out.append((tag, phonetic))
    return out


def assemble_segments(segments):
    """[(tag, phonetic), ...] -> 'phonetic1/(tag2)phonetic2/...'."""
    parts = [(f'({t}){p}' if t else p) for t, p in segments]
    return '/'.join(parts)


def normalize_reading(reading):
    """Rewrite a raw 音讀 string into the programmatic /+(tag) form."""
    segments = parse_segments(reading)
    if not segments:
        return reading
    return assemble_segments(segments)


def _transform_with_table(reading, table):
    """Parse, convert syllable digits per `table` on the phonetic part, reassemble."""
    segments = parse_segments(reading)
    if not segments:
        return reading
    if table is None:
        return assemble_segments(segments)

    def _sub(m):
        letters, digits = m.group(1), m.group(2)
        out = table.get(digits)
        return letters + (out if out is not None else digits)

    converted = [(t, _SYL_RE.sub(_sub, p)) for t, p in segments]
    return assemble_segments(converted)


def reading_to_kppy_input(reading, dialect):
    """Convert KPPY 數字式 (Chao pitch values) to KPPY_INPUT (internal digits 1-6)."""
    return _transform_with_table(reading, KPPY_PITCH_TO_INTERNAL.get(dialect))


def batch_convert_to_pfs(kppy_input_readings):
    """Call KonvertToPFS via Node.js to convert KPPY_INPUT → PFS_UNICODE and PFS_INPUT."""
    stdin_text = "\n".join(
        s if isinstance(s, str) and s.strip() else "" for s in kppy_input_readings
    )
    result = subprocess.run(
        ["node", CONVERT_JS],
        input=stdin_text, capture_output=True, text=True, check=True,
    )
    pfs_unicode = []
    pfs_input = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        pfs_unicode.append(parts[0] if len(parts) > 0 else "")
        pfs_input.append(parts[1] if len(parts) > 1 else "")
    while len(pfs_unicode) < len(kppy_input_readings):
        pfs_unicode.append("")
        pfs_input.append("")
    return pfs_unicode, pfs_input


def reading_digit_to_symbol(reading, dialect):
    """Convert a numeric-tone 音讀 string into the symbol form for the given dialect."""
    return _transform_with_table(reading, TONE_DIGIT_TO_SYMBOL.get(dialect))

def download_file(url, filepath):
    print(f"Downloading {url} to {filepath}...")
    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(filepath, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

def update_documentation(version_id, df_merged):
    print(f"Updating documentation with version {version_id}...")
    
    # Update README.md
    readme_path = "README.md"
    if os.path.exists(readme_path):
        with open(readme_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Update terminology: Sixian -> Si-yen, South Sixian -> Nam-si-yen
        content = content.replace("South Sixian", "Nam-si-yen")
        content = content.replace("Sixian", "Si-yen")
        
        # Update Version ID (pattern: * **Version ID**: YYYYMMDD-HHMM)
        content = re.sub(r'(\* \*\*Version ID\*\*: )\d{8}-\d{4}', rf'\g<1>{version_id}', content)
        # Update Date (pattern: * **Last Updated**: YYYY-MM-DD)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        content = re.sub(r'(\* \*\*Last Updated\*\*: )\d{4}-\d{2}-\d{2}', rf'\g<1>{today}', content)
        # Update Entries count
        dialect_counts = df_merged['Dialect'].value_counts()
        total = len(df_merged)
        parts = []
        for dialect, _, _, _ in PROCESSED_DIALECTS:
            n = int(dialect_counts.get(dialect, 0))
            if n:
                parts.append(f"{DIALECT_LABELS[dialect]} {n:,}")
        entries_line = f"* **Entries**: {total:,} (" + " + ".join(parts) + ")"
        content = re.sub(r'\* \*\*Entries\*\*: .+', entries_line, content)
        # Update Categories summary and table
        cat_counts = {}
        for idx_val in df_merged['詞目索引'].dropna():
            for m in re.finditer(r'詞目分類索引/(.+)', str(idx_val)):
                cat = m.group(1).strip()
                cat_counts[cat] = cat_counts.get(cat, 0) + 1
        tagged_rows = df_merged['詞目索引'].dropna().astype(str).str.contains('詞目分類索引/').sum()
        total_assignments = sum(cat_counts.values())
        n_cats = len(cat_counts)
        categories_line = f"* **Categories**: {n_cats} ({tagged_rows:,} entries tagged, {total_assignments:,} total assignments)"
        content = re.sub(r'\* \*\*Categories\*\*: .+', categories_line, content)
        # Rebuild the category table
        sorted_cats = sorted(cat_counts.items(), key=lambda x: -x[1])
        mid = (len(sorted_cats) + 1) // 2
        left = sorted_cats[:mid]
        right = sorted_cats[mid:]
        table_lines = ["| Category | Count | | Category | Count |",
                       "| --- | ---: | --- | --- | ---: |"]
        for i in range(max(len(left), len(right))):
            lc = f"| {left[i][0]} | {left[i][1]:,} " if i < len(left) else "| | "
            rc = f"| {right[i][0]} | {right[i][1]:,} |" if i < len(right) else "| | |"
            table_lines.append(lc + rc)
        new_table = "\n".join(table_lines)
        content = re.sub(
            r'\| Category \| Count \|[^\n]*\n(?:\| .+\n)*',
            new_table + "\n",
            content,
            count=1,
        )
        # Update syllable distribution table
        from collections import Counter
        syl_counts = Counter()
        for reading in df_merged['音讀'].dropna():
            primary = str(reading).split("/")[0].strip()
            syls = _SYL_RE.findall(primary)
            syl_counts[len(syls)] += 1
        # Bucket 10+ together
        buckets = {}
        for n, c in syl_counts.items():
            if n == 0:
                continue
            key = n if n < 10 else 10
            buckets[key] = buckets.get(key, 0) + c
        syl_total = sum(buckets.values())
        sorted_b = sorted(buckets.items())
        mid_s = (len(sorted_b) + 1) // 2
        left_s = sorted_b[:mid_s]
        right_s = sorted_b[mid_s:]
        syl_lines = ["| Syllables | Count | % | | Syllables | Count | % |",
                      "| ---: | ---: | ---: | --- | ---: | ---: | ---: |"]
        for i in range(max(len(left_s), len(right_s))):
            if i < len(left_s):
                n, c = left_s[i]
                lc = f"| {n} | {c:,} | {c/syl_total*100:.1f}% "
            else:
                lc = "| | | "
            if i < len(right_s):
                n, c = right_s[i]
                label = "10+" if n == 10 else str(n)
                rc = f"| {label} | {c:,} | {c/syl_total*100:.1f}% |"
            else:
                rc = "| | | |"
            syl_lines.append(lc + rc)
        new_syl_table = "\n".join(syl_lines)
        content = re.sub(
            r'\| Syllables \| Count \| % \|[^\n]*\n(?:\| .+\n)*',
            new_syl_table + "\n",
            content,
            count=1,
        )
        # Update download links and version references in prose
        content = re.sub(r'/public/\d{8}-\d{4}/', f'/public/{version_id}/', content)
        content = re.sub(r'`public/\d{8}-\d{4}/`', f'`public/{version_id}/`', content)
        
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(content)

def find_latest_version():
    """Return the latest existing version directory that has the processed-dialect ODS files, or None."""
    public = "public"
    if not os.path.isdir(public):
        return None
    versions = []
    for d in os.listdir(public):
        tangloo = os.path.join(public, d, "tangloo")
        if not os.path.isdir(tangloo):
            continue
        if all(os.path.isfile(os.path.join(tangloo, local)) for _, _, local, _ in PROCESSED_DIALECTS):
            versions.append(d)
    return max(versions) if versions else None


def main():
    import sys
    reuse_ods = "--reuse-ods" in sys.argv

    # 1. Create directory structure for the current version
    version_id = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    public_dir = os.path.join("public", version_id)
    tangloo_dir = os.path.join(public_dir, "tangloo")
    bunji_dir = os.path.join(public_dir, "bunji")

    os.makedirs(tangloo_dir, exist_ok=True)
    os.makedirs(bunji_dir, exist_ok=True)

    local_paths = {
        dialect: os.path.join(tangloo_dir, local)
        for dialect, _, local, _ in ALL_DIALECTS
    }
    upstream_urls = {
        dialect: f"{MOE_DOMAIN}{_UPSTREAM_DIR}/{upstream}"
        for dialect, upstream, _, _ in ALL_DIALECTS
    }

    # 2. Download ODS files (or reuse from latest version)
    if reuse_ods:
        latest = find_latest_version()
        if latest:
            import shutil
            src_dir = os.path.join("public", latest, "tangloo")
            print(f"Reusing ODS files from {latest}...")
            for dialect, _, local, _ in ALL_DIALECTS:
                src = os.path.join(src_dir, local)
                if os.path.isfile(src):
                    shutil.copy2(src, local_paths[dialect])
                else:
                    # New dialect not in prior version — fetch fresh.
                    download_file(upstream_urls[dialect], local_paths[dialect])
        else:
            print("No existing version found, downloading fresh...")
            for dialect in upstream_urls:
                download_file(upstream_urls[dialect], local_paths[dialect])
    else:
        for dialect in upstream_urls:
            download_file(upstream_urls[dialect], local_paths[dialect])

    # 3. Read ODS files (only the processed dialects — extras are mirrored as raw ODS only).
    dfs = []
    for dialect, _, _, _ in PROCESSED_DIALECTS:
        print(f"Reading {DIALECT_LABELS[dialect]} ({dialect}) ODS...")
        df = pd.read_excel(local_paths[dialect], engine="odf")
        df.insert(0, 'Dialect', dialect)
        dfs.append(df)

    # 4. Merge data
    print("Merging data...")
    df_merged = pd.concat(dfs, ignore_index=True)
    
    # Clean up column names (strip whitespace)
    df_merged.columns = df_merged.columns.str.strip()
    # Remove unnamed columns
    df_merged = df_merged.loc[:, ~df_merged.columns.str.contains('^Unnamed')]

    # Add the symbol-form 音讀 column right after the numeric-form column.
    if "音讀" in df_merged.columns:
        symbol_col = df_merged.apply(
            lambda r: reading_digit_to_symbol(r["音讀"], r["Dialect"]), axis=1
        )
        insert_at = df_merged.columns.get_loc("音讀") + 1
        df_merged.insert(insert_at, "音讀（符號）", symbol_col)

    # Convert to PFS via KonvertToPFS (KPPY 數字式 → KPPY_INPUT → PFS), per-segment
    # so variant markers stay structured and hyphenation is decided per reading.
    if "音讀" in df_merged.columns:
        print("Converting to PFS via KonvertToPFS...")
        parsed_rows = []
        flat_phonetics = []
        index_map = []  # (row_idx, seg_idx) per flat entry
        for row_idx, (_, r) in enumerate(df_merged.iterrows()):
            segs = parse_segments(r["音讀"])
            table = KPPY_PITCH_TO_INTERNAL.get(r["Dialect"])
            kppy_segs = []
            for seg_idx, (tag, phonetic) in enumerate(segs):
                if table is not None:
                    def _sub(m, tbl=table):
                        letters, digits = m.group(1), m.group(2)
                        out = tbl.get(digits)
                        return letters + (out if out is not None else digits)
                    phonetic = _SYL_RE.sub(_sub, phonetic)
                kppy_segs.append((tag, phonetic))
                flat_phonetics.append(phonetic)
                index_map.append((row_idx, seg_idx))
            parsed_rows.append(kppy_segs)

        pfs_uni_flat, pfs_inp_flat = batch_convert_to_pfs(flat_phonetics)

        pfs_uni_strings = [None] * len(parsed_rows)
        pfs_inp_strings = [None] * len(parsed_rows)
        # Initialize per-row segment lists from parsed_rows shape.
        uni_rows = [[(tag, "") for tag, _ in row] for row in parsed_rows]
        inp_rows = [[(tag, "") for tag, _ in row] for row in parsed_rows]
        for (row_idx, seg_idx), uni, inp in zip(index_map, pfs_uni_flat, pfs_inp_flat):
            tag = parsed_rows[row_idx][seg_idx][0]
            # Per-segment hyphenation: ≤4 space-separated syllables → hyphenate.
            uni_h = uni.replace(" ", "-") if uni and len(uni.split()) <= 4 else uni
            inp_h = inp.replace(" ", "-") if inp and len(inp.split()) <= 4 else inp
            uni_rows[row_idx][seg_idx] = (tag, uni_h)
            inp_rows[row_idx][seg_idx] = (tag, inp_h)

        for i in range(len(parsed_rows)):
            pfs_uni_strings[i] = assemble_segments(uni_rows[i])
            pfs_inp_strings[i] = assemble_segments(inp_rows[i])

        insert_at = df_merged.columns.get_loc("音讀（符號）") + 1
        df_merged.insert(insert_at, "PFS", pfs_uni_strings)
        df_merged.insert(insert_at + 1, "PFS輸入式", pfs_inp_strings)

    # Finally normalize the raw 音讀 column itself into the same programmatic form.
    if "音讀" in df_merged.columns:
        df_merged["音讀"] = df_merged["音讀"].apply(normalize_reading)

    # 5. Export to CSV and JSON
    csv_path = os.path.join(bunji_dir, "HakkaDictMoeData.csv")
    json_path = os.path.join(bunji_dir, "HakkaDictMoeData.json")
    
    print(f"Exporting to {csv_path}...")
    df_merged.to_csv(csv_path, index=False, encoding='utf-8')
    
    print(f"Exporting to {json_path}...")
    df_merged.to_json(json_path, orient='records', force_ascii=False, indent=2)
    
    # 6. Generate latest manifest
    files = {
        "csv": f"public/{version_id}/bunji/HakkaDictMoeData.csv",
        "json": f"public/{version_id}/bunji/HakkaDictMoeData.json",
    }
    for _, _, local, key in ALL_DIALECTS:
        files[f"ods_{key}"] = f"public/{version_id}/tangloo/{local}"
    manifest = {
        "latest_version": version_id,
        "updated_at": datetime.datetime.now().isoformat(),
        "files": files,
    }
    
    manifest_path = os.path.join("public", "manifest.json")
    print(f"Writing manifest to {manifest_path}...")
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    
    # 7. Update documentation
    update_documentation(version_id, df_merged)
        
    print("Build complete!")

if __name__ == "__main__":
    main()
