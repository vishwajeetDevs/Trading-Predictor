export interface Candle {
  symbol: string;
  timeframe: string;
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  source: string;
  is_closed: boolean;
  received_at: string;
}

export interface CandlePage {
  symbol: string | null;
  timeframe: string;
  count: number;
  candles: Candle[];
}

export interface Product {
  exchange_product_id: number;
  symbol: string;
  contract_type: string;
  state: string;
  trading_status: string | null;
  underlying_asset: string | null;
  quoting_asset: string | null;
  settling_asset: string | null;
  tick_size: string | null;
  contract_value: string | null;
  contract_unit_currency: string | null;
  notional_type: string | null;
  spot_index_symbol: string | null;
  description: string | null;
  short_description: string | null;
  settlement_time: string | null;
}

export interface Health {
  status: string;
  database: { connected?: boolean; error?: string | null };
  websocket: {
    connected?: boolean;
    subscriptions?: string[];
    last_message_at?: string | null;
    last_heartbeat_at?: string | null;
    heartbeat_age_seconds?: number | null;
    system_status?: string | null;
    last_error?: string | null;
    reconnect_count?: number;
    recovery_succeeded?: number;
    recovery_failed?: number;
    last_recovery_at?: string | null;
    last_recovery_status?: string | null;
    last_recovery_error?: string | null;
  };
  latest_market_event_at: string | null;
  latest_closed_candle_at: string | null;
  freshness_seconds: number | null;
  stale: boolean;
  recent_missing_candles: number;
  gaps: boolean;
  checked_at: string;
}

export interface Diagnostics {
  alembic_revision: string | null;
  tables: Record<string, boolean>;
  constraints: { uq_candle_slot?: boolean };
  counts: {
    products: number;
    candles: number;
    ingestion_state: number;
    websocket_events: number;
    data_quality_events: number;
    by_timeframe: Record<string, number>;
  };
  latest_received_at: string | null;
  recent_quality_events: Array<{
    occurred_at: string | null;
    kind: string;
    timeframe: string | null;
    detail: unknown;
  }>;
  websocket: {
    connected: boolean;
    reconnect_count: number;
    last_message_at: string | null;
    last_error: string | null;
  };
}

export interface StatusPayload {
  product: { symbol?: string; exchange_product_id?: number } | null;
  streams: Array<{
    timeframe: string;
    stream: string;
    status: string;
    oldest_open_time: string | null;
    newest_open_time: string | null;
    last_success_at: string | null;
  }>;
  quality_events_24h: Record<string, number>;
}

export interface DeltaProbe {
  ok: boolean;
  symbol?: string;
  candle_count?: number;
  error?: string;
  checked_at: string;
}

export interface ApiResult {
  path: string;
  status: number | null;
  ms: number;
  ok: boolean;
  body: unknown;
  error: string | null;
  testedAt: string;
}
