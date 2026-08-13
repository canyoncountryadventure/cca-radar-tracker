import { getSql } from './db.js';

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ ok: false });
  }

  try {
    const sql = getSql();
    const rows = await sql`
      SELECT observed_at, station_name, temperature_c
      FROM telemetry_readings
      WHERE temperature_c IS NOT NULL
      ORDER BY observed_at DESC
      LIMIT 1
    `;

    return res.status(200).json({
      ok: true,
      database: true,
      latest: rows[0] ?? null,
    });
  } catch (error) {
    console.error('Health check failed', error);
    return res.status(500).json({ ok: false, database: false });
  }
}
