CREATE TABLE IF NOT EXISTS telemetry_readings (
  id BIGSERIAL PRIMARY KEY,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  observed_at TIMESTAMPTZ NOT NULL,
  node_num BIGINT,
  station_name TEXT NOT NULL,
  telemetry_type TEXT NOT NULL DEFAULT 'environment',
  temperature_c DOUBLE PRECISION,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  radio JSONB NOT NULL DEFAULT '{}'::jsonb,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS telemetry_readings_station_time_idx
  ON telemetry_readings (station_name, observed_at DESC);

CREATE INDEX IF NOT EXISTS telemetry_readings_node_time_idx
  ON telemetry_readings (node_num, observed_at DESC);
