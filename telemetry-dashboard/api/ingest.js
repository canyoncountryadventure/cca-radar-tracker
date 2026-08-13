import { getSql } from './db.js';

function parsePossibleJson(value) {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function unwrapBody(input) {
  let body = parsePossibleJson(input);

  for (let i = 0; i < 4; i += 1) {
    if (!body || typeof body !== 'object' || Array.isArray(body)) break;

    if (body.type === 'telemetry' && body.payload && typeof body.payload === 'object') {
      return body;
    }

    const candidates = [body.payload, body.body, body.message, body.data];
    const next = candidates.map(parsePossibleJson).find(
      (value) => value && typeof value === 'object' && !Array.isArray(value),
    );

    if (!next || next === body) break;
    body = next;
  }

  return body;
}

function nodeHex(nodeNum) {
  if (!Number.isFinite(nodeNum)) return 'unknown';
  return Math.trunc(nodeNum).toString(16).padStart(8, '0');
}

function stationNameFor(body, nodeNum) {
  const configuredName = process.env.STATION_NAME?.trim();
  if (configuredName) return configuredName;

  const supplied = body.station_name || body.station || body.sender_name;
  if (typeof supplied === 'string' && supplied.trim()) return supplied.trim();

  return `Node ${nodeHex(nodeNum)}`;
}

function readIngestKey(req) {
  const headerValue = req.headers['x-ingest-key'];
  if (typeof headerValue === 'string') return headerValue;

  const authorization = req.headers.authorization;
  if (typeof authorization === 'string' && authorization.startsWith('Bearer ')) {
    return authorization.slice(7);
  }

  return '';
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ ok: false, error: 'POST required' });
  }

  const expectedKey = process.env.INGEST_KEY;
  if (!expectedKey) {
    return res.status(500).json({ ok: false, error: 'INGEST_KEY is not configured' });
  }

  if (readIngestKey(req) !== expectedKey) {
    return res.status(401).json({ ok: false, error: 'Unauthorized' });
  }

  const body = unwrapBody(req.body);
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return res.status(400).json({ ok: false, error: 'Expected a JSON object' });
  }

  if (body.type !== 'telemetry') {
    return res.status(202).json({ ok: true, stored: false, reason: 'Not telemetry' });
  }

  const metrics = body.payload;
  if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) {
    return res.status(202).json({ ok: true, stored: false, reason: 'Telemetry has no metrics payload' });
  }

  const temperature = Number(metrics.temperature);
  if (!Number.isFinite(temperature)) {
    return res.status(202).json({ ok: true, stored: false, reason: 'No temperature in telemetry payload' });
  }

  const nodeNum = Number(body.from);
  const timestampSeconds = Number(body.timestamp);
  const observedAt = Number.isFinite(timestampSeconds) && timestampSeconds > 0
    ? new Date(timestampSeconds * 1000)
    : new Date();

  const stationName = stationNameFor(body, nodeNum);
  const radio = {
    rssi: Number.isFinite(Number(body.rssi)) ? Number(body.rssi) : null,
    snr: Number.isFinite(Number(body.snr)) ? Number(body.snr) : null,
    hop_start: Number.isFinite(Number(body.hop_start)) ? Number(body.hop_start) : null,
    hops_away: Number.isFinite(Number(body.hops_away)) ? Number(body.hops_away) : null,
    channel: Number.isFinite(Number(body.channel)) ? Number(body.channel) : null,
    gateway: body.sender ?? null,
  };

  try {
    const sql = getSql();
    const rows = await sql`
      INSERT INTO telemetry_readings (
        observed_at,
        node_num,
        station_name,
        telemetry_type,
        temperature_c,
        metrics,
        radio,
        raw
      ) VALUES (
        ${observedAt.toISOString()},
        ${Number.isFinite(nodeNum) ? Math.trunc(nodeNum) : null},
        ${stationName},
        'environment',
        ${temperature},
        ${JSON.stringify(metrics)}::jsonb,
        ${JSON.stringify(radio)}::jsonb,
        ${JSON.stringify(body)}::jsonb
      )
      RETURNING id, observed_at, station_name, temperature_c
    `;

    return res.status(201).json({ ok: true, stored: true, reading: rows[0] });
  } catch (error) {
    console.error('Telemetry ingest failed', error);
    return res.status(500).json({ ok: false, error: 'Database insert failed' });
  }
}
