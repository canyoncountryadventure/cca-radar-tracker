import { getSql } from './db.js';

function clampInt(value, fallback, min, max) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ ok: false, error: 'GET required' });
  }

  const hours = clampInt(req.query.hours, 24, 1, 24 * 365);
  const limit = clampInt(req.query.limit, 500, 1, 5000);

  try {
    const sql = getSql();
    const rows = await sql`
      SELECT
        id,
        observed_at,
        received_at,
        node_num,
        station_name,
        temperature_c,
        metrics,
        radio
      FROM telemetry_readings
      WHERE observed_at >= NOW() - (${hours} * INTERVAL '1 hour')
        AND temperature_c IS NOT NULL
      ORDER BY observed_at DESC
      LIMIT ${limit}
    `;

    return res.status(200).json({ ok: true, hours, readings: rows });
  } catch (error) {
    console.error('Telemetry query failed', error);
    return res.status(500).json({ ok: false, error: 'Database query failed' });
  }
}
