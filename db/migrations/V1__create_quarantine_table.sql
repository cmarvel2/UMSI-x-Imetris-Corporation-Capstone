-- Create a new table 'TableName' with a primary key and columns
CREATE TABLE IF NOT EXISTS quarantine_table (
    quarantine_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quarantined_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    quarantine_reason TEXT[],
    source_file TEXT,
    resolution_status TEXT DEFAULT 'unresolved',
    resolved_at TIMESTAMPTZ
);