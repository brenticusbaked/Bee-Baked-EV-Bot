create table if not exists execution_orders (
    order_id text primary key,
    symbol text not null,
    side text not null,
    quantity numeric not null,
    limit_price numeric,
    fair_price numeric,
    order_type text,
    time_in_force text,
    strategy text,
    source_signal text,
    status text not null,
    rejected_reason text,
    filled_quantity numeric,
    fill_rate numeric,
    average_price numeric,
    slippage numeric,
    edge_capture numeric,
    fees numeric,
    metadata jsonb default '{}'::jsonb,
    created_at timestamptz,
    logged_at timestamptz default now()
);

create table if not exists execution_child_orders (
    child_order_id text primary key,
    parent_order_id text not null references execution_orders(order_id) on delete cascade,
    venue_id text not null,
    symbol text not null,
    side text not null,
    quantity numeric not null,
    limit_price numeric,
    route_score numeric,
    status text not null,
    metadata jsonb default '{}'::jsonb,
    logged_at timestamptz default now()
);

create table if not exists execution_fills (
    fill_id text primary key,
    child_order_id text not null references execution_child_orders(child_order_id) on delete cascade,
    parent_order_id text not null references execution_orders(order_id) on delete cascade,
    venue_id text not null,
    symbol text not null,
    side text not null,
    quantity numeric not null,
    price numeric not null,
    fee numeric default 0,
    filled_at timestamptz
);

create table if not exists venue_metrics (
    metric_id text primary key,
    parent_order_id text not null references execution_orders(order_id) on delete cascade,
    child_order_id text not null references execution_child_orders(child_order_id) on delete cascade,
    venue_id text not null,
    symbol text not null,
    status text not null,
    routed_quantity numeric not null,
    filled_quantity numeric not null,
    fill_rate numeric not null,
    average_fill_price numeric,
    route_score numeric,
    fee numeric default 0,
    latency_ms integer,
    fill_probability numeric,
    edge_capture numeric,
    measured_at timestamptz default now()
);

create index if not exists idx_execution_orders_logged_at on execution_orders(logged_at desc);
create index if not exists idx_execution_orders_source_signal on execution_orders(source_signal);
create index if not exists idx_execution_child_orders_venue on execution_child_orders(venue_id);
create index if not exists idx_execution_fills_parent on execution_fills(parent_order_id);
create index if not exists idx_venue_metrics_venue_measured on venue_metrics(venue_id, measured_at desc);
