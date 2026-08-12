from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "static" / "data"
UNIFIED_XLSX = DATA_DIR / "Coolattin unified database.xlsx"
OUT_CSV = DATA_DIR / "unified_processed.csv"


def _clean_text(value: object) -> str | None:
    if pd.isna(value):
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def _clean_name(value: object) -> str | None:
    s = _clean_text(value)
    if not s:
        return None
    s = re.sub(r"\s+repts\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+jun\.?\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+sen\.?\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+pp\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+widow\b", " Widow", s, flags=re.IGNORECASE)
    s = s.replace("&", " and ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.title() if s else None


def _clean_townland(value: object) -> str | None:
    s = _clean_text(value)
    if not s:
        return None
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\"[^\"]*\"", "", s)
    s = re.sub(r"struck\s*out", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    if not s or s == "?":
        return None
    return s.title()


def _clean_year(value: object) -> int | None:
    if pd.isna(value):
        return None
    s = str(value).strip()
    if not s:
        return None
    if "-" in s:
        s = s.split("-", 1)[0].strip()
    try:
        y = int(float(s))
    except Exception:
        return None
    if y < 1000 or y > 2100:
        return None
    return y


def _clean_month(value: object) -> str | None:
    s = _clean_text(value)
    if not s:
        return None
    key = s[:3].lower()
    month_map = {
        "jan": "January",
        "feb": "February",
        "mar": "March",
        "apr": "April",
        "may": "May",
        "jun": "June",
        "jul": "July",
        "aug": "August",
        "sep": "September",
        "oct": "October",
        "nov": "November",
        "dec": "December",
    }
    return month_map.get(key, s.title())


def _has_multiple_surnames(value: object) -> bool:
    s = _clean_text(value)
    if not s:
        return False
    return bool(re.search(r"[;/,&]| and ", s.lower()))


def _to_snake(label: str) -> str:
    s = label.strip().lower()
    s = s.replace("£", "")
    s = s.replace("/", " ")
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _interpret_abbrev_terms(*values: object) -> str | None:
    text = " ".join([str(v) for v in values if v is not None and not (isinstance(v, float) and pd.isna(v))]).lower()
    if not text.strip():
        return None

    hits: list[str] = []
    if re.search(r"\bdo\.?\b", text):
        hits.append("Do. means Ditto (same as above).")
    if re.search(r"\bexors?\.?\b", text):
        hits.append("Exors. means Executors/Representatives handling a deceased tenant's affairs.")
    if re.search(r"\brepts?\.?\b", text):
        hits.append("Repts. means Representatives.")
    if re.search(r"\bviz\.?\b", text):
        hits.append("Viz. means namely/that is to say.")
    if re.search(r"\bjunr?\.?\b|\bjunior\b", text):
        hits.append("Junr means Junior.")
    if re.search(r"\bsnr?\.?\b|\bsenior\b", text):
        hits.append("Snr means Senior.")
    if re.search(r"\bpoor\s*house\b", text):
        hits.append("Poor House refers to workhouse context in historical usage.")

    if not hits:
        return None
    return " ".join(hits)


def _interpret_land_units(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if re.search(r"\ba\.?\s*:?\s*r\.?\s*:?\s*p\.?\b", s):
        return "a:r:p means Acres, Roods, and Perches (1 acre = 4 roods; 1 rood = 40 perches)."
    if re.search(r"\brood\b", s):
        return "Rood is a land unit (1 acre = 4 roods)."
    if re.search(r"\bperch\b", s):
        return "Perch is the smallest common land unit in this system (1 rood = 40 perches)."
    return None


def _interpret_tenancy_context(chief_surname: object, under_surname: object, mountains_common: object) -> str | None:
    notes: list[str] = []
    if chief_surname and not (isinstance(chief_surname, float) and pd.isna(chief_surname)):
        notes.append("Chief tenant indicates a principal leaseholder who could sublet to under-tenants.")
    if under_surname and not (isinstance(under_surname, float) and pd.isna(under_surname)):
        notes.append("Under-tenant indicates land rented from a chief tenant rather than directly from the estate owner.")
    if bool(mountains_common):
        notes.append("Mountain in common indicates shared grazing/turf rights on common land.")
    if not notes:
        return None
    return " ".join(notes)


def preprocess_unified() -> pd.DataFrame:
    df = pd.read_excel(UNIFIED_XLSX, engine="openpyxl")

    aliased_name_cols = {
        "Surname (Chief tenant)": "chief_tenant_surname_original",
        "Forename (Chief Tenant)": "chief_tenant_forename_original",
        "Surname (Undertenant)": "under_tenant_surname_original",
        "Forename (Undertenant)": "under_tenant_forename_original",
    }

    all_cols = {}
    for col in df.columns:
        if col in aliased_name_cols:
            continue
        key = _to_snake(col)
        if not key:
            continue
        base = key
        i = 2
        while key in all_cols:
            key = f"{base}_{i}"
            i += 1
        all_cols[key] = col

    out = pd.DataFrame(index=df.index)
    for k, original_col in all_cols.items():
        out[k] = df[original_col].map(_clean_text)

    for original_col, alias_key in aliased_name_cols.items():
        out[alias_key] = df[original_col].map(_clean_text)

    out["record_id"] = df["Unique ID No"].map(_clean_text)
    out["surname"] = df["Surname"].map(_clean_name)
    out["forename"] = df["Forename"].map(_clean_name)
    townland_official = df["Townland official name"].map(_clean_townland)
    townland_shown = df["Townland as shown"].map(_clean_townland)
    out["townland"] = townland_official.fillna(townland_shown)
    out["year"] = df["Year"].map(_clean_year).astype("Int64")
    out["month"] = df["Month "].map(_clean_month)
    out["estate"] = df["Estate"].map(_clean_text)
    out["parish"] = df["Parish (Civil)"].map(_clean_text)
    out["gender"] = df["Gender"].map(_clean_text)
    out["occupation"] = df["Occupation"].map(_clean_text)
    out["role"] = df["Relationship to head of household"].map(_clean_text)
    out["chief_tenant_surname"] = df["Surname (Chief tenant)"].map(_clean_name)
    out["chief_tenant_forename"] = df["Forename (Chief Tenant)"].map(_clean_name)
    out["under_tenant_surname"] = df["Surname (Undertenant)"].map(_clean_name)
    out["under_tenant_forename"] = df["Forename (Undertenant)"].map(_clean_name)
    out["household_list"] = df["Household list in Emigration Records"].map(_clean_text)
    out["legal_action"] = df["Legal Action"].map(_clean_text)
    out["acres_irish"] = df["Acres (Irish)"].map(_clean_text)
    out["acres_english"] = pd.to_numeric(df["Acres (English)"], errors="coerce")
    out["rent_owed"] = df["Rent Owed £"].map(_clean_text)
    out["arrears"] = df["Arrears"].map(_clean_text)
    out["age"] = df["Age"].map(_clean_text)
    out["nli_ref"] = df["NLI Ref"].map(_clean_text)
    out["volume"] = pd.to_numeric(df["Vol"], errors="coerce")
    out["page"] = pd.to_numeric(df["Page"], errors="coerce")
    out["ship_name"] = df["NAME OF SHIP"].map(_clean_text)
    out["departure"] = df["PLACE AND DATE OF DEPARTURE "].map(_clean_text)
    out["arrival"] = df["PLACE AND DATE OF ARRIVAL"].map(_clean_text)
    out["holding_on_estate"] = df["HOLDING ON FITZW ESTATE"].map(_clean_text)
    out["comments"] = df["Comments"].map(_clean_text)
    out["mountains_in_common"] = out["holding_on_estate"].fillna("").str.contains(
        "mountain", case=False, regex=True
    )
    out["flag_multiple_surnames"] = df["Surname"].map(_has_multiple_surnames)
    out["canonical_name"] = (
        out["forename"].fillna("").str.strip() + " " + out["surname"].fillna("").str.strip()
    ).str.strip().replace("", pd.NA)
    out["family_key"] = (
        out["surname"].fillna("").str.lower().str.strip()
        + "|"
        + out["townland"].fillna("").str.lower().str.strip()
    )

    out["has_emigration_record"] = (
        out["ship_name"].notna()
        | out["departure"].notna()
        | out["arrival"].notna()
        | out["household_list"].notna()
    )
    out["has_eviction_record"] = (
        out["legal_action"].notna()
        | out["rent_owed"].notna()
        | out["arrears"].notna()
    )
    out["has_tenancy_record"] = (
        out["chief_tenant_surname"].notna()
        | out["under_tenant_surname"].notna()
        | out["acres_irish"].notna()
        | out["occupation"].notna()
    )

    out["interpreted_abbreviations"] = [
        _interpret_abbrev_terms(a, b, c, d, e)
        for a, b, c, d, e in zip(
            out.get("legal_action", pd.Series([None] * len(out))),
            out.get("comments", pd.Series([None] * len(out))),
            out.get("surname", pd.Series([None] * len(out))),
            out.get("forename", pd.Series([None] * len(out))),
            out.get("household_list", pd.Series([None] * len(out))),
            strict=False,
        )
    ]
    out["interpreted_land_units"] = [
        _interpret_land_units(v)
        for v in out.get("acres", pd.Series([None] * len(out)))
    ]
    out["interpreted_tenancy_context"] = [
        _interpret_tenancy_context(ch, ud, mc)
        for ch, ud, mc in zip(
            out.get("chief_tenant_surname", pd.Series([None] * len(out))),
            out.get("under_tenant_surname", pd.Series([None] * len(out))),
            out.get("mountains_in_common", pd.Series([False] * len(out))),
            strict=False,
        )
    ]

    out = out.drop_duplicates(subset=["record_id"], keep="first")
    return out


def preprocess() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = preprocess_unified()
    out.to_csv(OUT_CSV, index=False)
    print(f"Saved unified processed dataset: {OUT_CSV}")
    print(f"Records: {len(out)}")
    print(f"Townlands: {out['townland'].dropna().nunique()}")
    print(f"Surnames: {out['surname'].dropna().nunique()}")


if __name__ == "__main__":
    preprocess()
