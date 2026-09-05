"""Pandas boundary for SQLite datasets; no CSV serialization in runtime mode."""
from __future__ import annotations

from datetime import date, datetime
import itertools
import json

import pandas as pd


def frame_rows(frame):
    def native(value):
        if isinstance(value, (datetime, date)):
            return None if pd.isna(value) else value.isoformat()
        if isinstance(value, (list, dict)):
            return value
        if pd.isna(value):
            return None
        return value.item() if hasattr(value, "item") else value
    for values in frame.itertuples(index=False, name=None):
        yield {str(key): native(value) for key, value in zip(frame.columns, values)}


def read_frame(name, path, **kwargs):
    from runtime_db import RuntimeDatabase, database_enabled
    if not database_enabled():
        return pd.read_csv(path, **kwargs)
    database = RuntimeDatabase()
    meta = database.dataset_metadata(name)
    if meta is None:
        raise KeyError(f"DB dataset missing: {name}; import legacy data explicitly")
    fields = json.loads(meta["fieldnames_json"])
    usecols = kwargs.pop("usecols", None)
    if usecols is not None:
        wanted = [field for field in fields if usecols(field)] if callable(usecols) else list(usecols)
        if not set(wanted) <= set(fields):
            raise ValueError(f"missing columns in {name}: {set(wanted) - set(fields)}")
        fields = [field for field in fields if field in wanted]
    dtype = kwargs.pop("dtype", None)
    parse_dates = kwargs.pop("parse_dates", [])
    nrows = kwargs.pop("nrows", None)
    chunksize = kwargs.pop("chunksize", None)
    default_na = kwargs.pop("keep_default_na", True)
    for harmless in ("low_memory", "encoding", "engine", "on_bad_lines"):
        kwargs.pop(harmless, None)
    if kwargs:
        raise TypeError(f"Unsupported DB dataframe options: {sorted(kwargs)}")

    def typed(rows):
        frame = pd.DataFrame.from_records(rows, columns=fields)
        for column in fields:
            series = frame[column]
            if default_na:
                series = series.replace({"": None, "NaN": None, "nan": None,
                                         "NA": None, "N/A": None, "null": None, "None": None})
            target = dtype.get(column) if isinstance(dtype, dict) else dtype
            if target is not None:
                # CSV's dtype=str retains NA cells instead of spelling them 'None'.
                series = series.map(lambda v: str(v) if pd.notna(v) else float("nan")) if target in (str, "str") else series.astype(target)
            elif len(series):
                present = series.dropna()
                if len(present) and present.map(lambda v: isinstance(v, bool) or str(v) in ("True", "False")).all():
                    series = series.map(lambda v: v if isinstance(v, bool) else {"True": True, "False": False}.get(v))
                else:
                    converted = pd.to_numeric(series, errors="coerce")
                    if converted.notna().sum() == series.notna().sum():
                        series = converted
            frame[column] = series
        for column in parse_dates or []:
            frame[column] = pd.to_datetime(frame[column])
        return frame

    rows = ({field: row.get(field) for field in fields} for row in database.iter_dataset(name))
    if nrows is not None:
        rows = itertools.islice(rows, nrows)
    if chunksize:
        def chunks():
            while batch := list(itertools.islice(rows, chunksize)):
                yield typed(batch)
        return chunks()
    return typed(rows)
