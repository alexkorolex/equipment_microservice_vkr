CREATE SCHEMA IF NOT EXISTS equipment;

CREATE TABLE IF NOT EXISTS equipment.equipment (
    id UUID PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    inventory_number TEXT NOT NULL,
    name TEXT NOT NULL,
    catalog_item_id TEXT NULL,
    purchase_price NUMERIC(18, 2) NOT NULL CHECK (purchase_price >= 0),
    residual_value NUMERIC(18, 2) NOT NULL CHECK (residual_value >= 0),
    useful_life_months INTEGER NOT NULL CHECK (useful_life_months > 0),
    fuel_type TEXT NOT NULL
        CHECK (fuel_type IN ('diesel', 'petrol', 'gas', 'electric', 'none')),
    fuel_consumption_per_hour NUMERIC(12, 3) NOT NULL CHECK (fuel_consumption_per_hour >= 0),
    daily_overhead NUMERIC(18, 2) NOT NULL CHECK (daily_overhead >= 0),
    currency CHAR(3) NOT NULL,
    status TEXT NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'maintenance', 'decommissioned')),
    version INTEGER NOT NULL DEFAULT 1,
    created_by_user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT equipment_residual_le_purchase CHECK (residual_value <= purchase_price),
    CONSTRAINT equipment_inventory_number_unique UNIQUE (organization_id, inventory_number)
);

CREATE INDEX IF NOT EXISTS equipment_organization_idx
    ON equipment.equipment (organization_id, inventory_number);

CREATE TABLE IF NOT EXISTS equipment.rentals (
    id UUID PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    equipment_id UUID NOT NULL REFERENCES equipment.equipment (id),
    worker_user_id BIGINT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    returned_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'returned', 'cancelled')),
    shift_hours_per_day NUMERIC(4, 2) NOT NULL
        CHECK (shift_hours_per_day > 0 AND shift_hours_per_day <= 24),
    purpose TEXT NOT NULL DEFAULT '',
    created_by_user_id BIGINT NOT NULL,
    external_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (external_status IN ('pending', 'submitted', 'failed')),
    external_reference TEXT NULL,
    costs_complete BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT rentals_period_valid CHECK (ends_at > starts_at),
    CONSTRAINT rentals_return_after_start CHECK (returned_at IS NULL OR returned_at >= starts_at),
    CONSTRAINT rentals_returned_has_time CHECK ((status = 'returned') = (returned_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS rentals_equipment_idx
    ON equipment.rentals (equipment_id, status);
CREATE INDEX IF NOT EXISTS rentals_organization_idx
    ON equipment.rentals (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS rentals_accrual_idx
    ON equipment.rentals (starts_at)
    WHERE status IN ('open', 'returned') AND NOT costs_complete;

CREATE TABLE IF NOT EXISTS equipment.cost_accruals (
    id UUID PRIMARY KEY,
    rental_id UUID NOT NULL REFERENCES equipment.rentals (id),
    accrual_date DATE NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    currency CHAR(3) NOT NULL,
    external_calculation_id TEXT NULL,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT cost_accruals_period_valid CHECK (period_end > period_start),
    CONSTRAINT cost_accruals_one_per_day UNIQUE (rental_id, accrual_date)
);

CREATE TABLE IF NOT EXISTS equipment.cost_lines (
    accrual_id UUID NOT NULL REFERENCES equipment.cost_accruals (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    cost_type TEXT NOT NULL,
    amount NUMERIC(18, 4) NOT NULL,
    quantity NUMERIC(18, 4) NULL,
    unit TEXT NULL,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (accrual_id, position)
);

-- Messages for the external cost system, written in the transaction that changed
-- the rental and delivered by the submission worker.
CREATE TABLE IF NOT EXISTS equipment.outbox (
    id UUID PRIMARY KEY,
    rental_id UUID NOT NULL REFERENCES equipment.rentals (id),
    kind TEXT NOT NULL
        CHECK (kind IN ('rental_opened', 'rental_returned', 'rental_cancelled')),
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    -- New messages are due at once, whatever the application clock says.
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT '-infinity',
    locked_until TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    sent_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS outbox_pending_idx
    ON equipment.outbox (next_attempt_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS outbox_rental_idx
    ON equipment.outbox (rental_id, created_at);
