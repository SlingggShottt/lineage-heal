with

files as (
    select * from {{ source('raw_zone', 'files') }}
),

-- Path and file name carry the identity: raw/source=<s>/dataset=<d>/trade_date=<date>/<hash>__<name>.
-- Reading them with patterns keeps this model independent of the readers' partition detection.
-- TODO(LH-086): reconcile last_modified with the ingestion log, which is the authoritative record.
versions as (
    select
        regexp_extract(filename, 'source=([^/]+)/', 1) as source,
        regexp_extract(filename, 'dataset=([^/]+)/', 1) as dataset,
        try_cast(regexp_extract(filename, 'trade_date=([0-9]{4}-[0-9]{2}-[0-9]{2})/', 1) as date) as trade_date,
        regexp_extract(filename, '/([0-9a-f]{16})__[^/]+$', 1) as version,
        filename as file_path,
        size as size_bytes,
        last_modified as arrived_at,
    from files
)

select * from versions
