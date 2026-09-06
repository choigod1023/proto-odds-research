"""Public performance index: one canonical saved prediction per event."""
def performance_index(records):
    fields = ('prediction_snapshot_id','selection_id','market','label','selection','odds','probability','captured_at','result','settled_at')
    return {'scope':'all_ledger_predictions','records':{
        event:{key:record.get(key) for key in fields} for event,record in records.items()
    }}
