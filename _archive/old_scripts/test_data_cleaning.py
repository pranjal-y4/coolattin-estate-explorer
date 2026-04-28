import pandas as pd
import sys, pathlib
# Ensure the project root is on the import path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from coolattin.scripts.data_cleaning import standardize_dates, normalize_names

def test_standardize_dates():
    df = pd.DataFrame({"date_col": ["01/02/1841", "1842-03-04", "invalid"]})
    cleaned = standardize_dates(df, ["date_col"])
    assert cleaned.loc[0, "date_col"] == "1841-01-02"
    assert cleaned.loc[1, "date_col"] == "1842-03-04"
    assert pd.isna(cleaned.loc[2, "date_col"]) or cleaned.loc[2, "date_col"] == "NaT"

def test_normalize_names():
    df = pd.DataFrame({"surname": ["  o'connor ", "SMITH&JONES"]})
    cleaned = normalize_names(df, ["surname"])
    assert cleaned.loc[0, "surname"] == "O'Connor"
    assert cleaned.loc[1, "surname"] == "Smith And Jones"
