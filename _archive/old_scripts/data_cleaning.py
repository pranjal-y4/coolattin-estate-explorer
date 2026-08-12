import pandas as pd
from datetime import datetime

def standardize_dates(df, date_columns):
    for col in date_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d')
    return df

def normalize_names(df, name_columns):
    for col in name_columns:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
                .str.replace(r"\s+", " ", regex=True)
                .str.replace(r"\s*&\s*", " and ", regex=True)
                .str.title()
            )
    return df

def clean_unified(df):
    date_cols = [c for c in df.columns if "date" in c.lower()]
    name_cols = [c for c in df.columns if any(key in c.lower() for key in ["name", "forename", "surname"])]
    df = standardize_dates(df, date_cols)
    df = normalize_names(df, name_cols)
    return df
