"""Import one authorized HPIS 1.0 JSON context on the terminal."""

import json
from pathlib import Path


async def import_context(
    device, *, database, crypto, master_key, id_counter, data_counter, records=None
):
    aid = crypto.anonym_id(master_key, id_counter)
    dek = crypto.k_data(master_key, data_counter)
    entry = json.loads(Path(database).read_text(encoding="utf8"))[aid]
    context = json.loads(crypto.aead_decrypt(dek, entry["C"]))
    # Applications can supply a mapping for their own ContextData schema.
    rows = (
        records(context) if records is not None else context["ContextData"]["records"]
    )
    batch, count = [], 0
    for row in rows:
        batch.append(row)
        if len(batch) == 128:
            await device.put(batch)
            count += len(batch)
            batch.clear()
    if batch:
        await device.put(batch)
        count += len(batch)
    return count
