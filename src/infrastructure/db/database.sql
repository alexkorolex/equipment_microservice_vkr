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

-- Plate, vehicle type, fuel by mileage and the FortMonitor link (added later).
ALTER TABLE equipment.equipment ADD COLUMN IF NOT EXISTS registration_number TEXT NULL;
ALTER TABLE equipment.equipment ADD COLUMN IF NOT EXISTS vehicle_type_id BIGINT NULL;
ALTER TABLE equipment.equipment
    ADD COLUMN IF NOT EXISTS vehicle_type_name TEXT NOT NULL DEFAULT '';
ALTER TABLE equipment.equipment
    ADD COLUMN IF NOT EXISTS fuel_consumption_mode TEXT NOT NULL DEFAULT 'per_hour'
        CHECK (fuel_consumption_mode IN ('per_hour', 'per_100km'));
ALTER TABLE equipment.equipment
    ADD COLUMN IF NOT EXISTS fuel_consumption_per_100km NUMERIC(12, 3) NOT NULL DEFAULT 0
        CHECK (fuel_consumption_per_100km >= 0);
ALTER TABLE equipment.equipment ADD COLUMN IF NOT EXISTS telematics_object_id BIGINT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS equipment_registration_number_unique
    ON equipment.equipment (organization_id, registration_number)
    WHERE registration_number IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS equipment_telematics_object_unique
    ON equipment.equipment (organization_id, telematics_object_id)
    WHERE telematics_object_id IS NOT NULL;

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

-- Odometer readings, km: the mileage of per_100km equipment without a tracker.
ALTER TABLE equipment.rentals ADD COLUMN IF NOT EXISTS odometer_start NUMERIC(12, 1) NULL
    CHECK (odometer_start >= 0);
ALTER TABLE equipment.rentals ADD COLUMN IF NOT EXISTS odometer_end NUMERIC(12, 1) NULL
    CHECK (odometer_end >= 0);
DO $$ BEGIN
    ALTER TABLE equipment.rentals ADD CONSTRAINT rentals_odometer_forward
        CHECK (odometer_end IS NULL OR odometer_start IS NULL OR odometer_end >= odometer_start);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

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

-- The organization's FortMonitor account; the password is encrypted by the service.
CREATE TABLE IF NOT EXISTS equipment.telematics_accounts (
    organization_id BIGINT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'fortmonitor' CHECK (provider IN ('fortmonitor')),
    login TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    updated_by_user_id BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Change events for live updates in the CRM (realtime-gateway). Triggers write them
-- in the transaction of the change itself, from every write path, background workers
-- included; ChangePublisher sends them to Kafka "crm.changes" and marks them.
CREATE TABLE IF NOT EXISTS equipment.change_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id BIGINT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event TEXT NOT NULL,
    version BIGINT NULL,
    -- Who may see the change besides the section's permission (a rental's worker).
    user_id BIGINT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    published_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS change_events_unpublished
    ON equipment.change_events (occurred_at) WHERE published_at IS NULL;

-- Arguments: entity, organization column, id column[, event suffix ('' for the
-- operation's)[, column of the user who may see it regardless of permissions]].
CREATE OR REPLACE FUNCTION equipment.record_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    item jsonb := to_jsonb(CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END);
    suffix text := CASE
        WHEN TG_NARGS > 3 AND TG_ARGV[3] <> '' THEN TG_ARGV[3]
        WHEN TG_OP = 'INSERT' THEN 'created'
        WHEN TG_OP = 'UPDATE' THEN 'updated'
        ELSE 'deleted'
    END;
BEGIN
    IF TG_OP = 'UPDATE' AND to_jsonb(NEW) = to_jsonb(OLD) THEN
        RETURN NULL;
    END IF;
    INSERT INTO equipment.change_events (
        organization_id, entity, entity_id, event, version, user_id
    )
    VALUES (
        (item ->> TG_ARGV[1])::bigint,
        TG_ARGV[0],
        item ->> TG_ARGV[2],
        TG_ARGV[0] || '.' || suffix,
        (item ->> 'version')::bigint,
        CASE WHEN TG_NARGS > 4 THEN (item ->> TG_ARGV[4])::bigint END
    );
    RETURN NULL;
END $$;

-- Cost accruals have no organization of their own: they change their rental.
CREATE OR REPLACE FUNCTION equipment.record_accrual_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO equipment.change_events (organization_id, entity, entity_id, event, user_id)
    SELECT r.organization_id, 'rental', r.id::text, 'rental.updated', r.worker_user_id
    FROM equipment.rentals r
    WHERE r.id = (CASE WHEN TG_OP = 'DELETE' THEN OLD.rental_id ELSE NEW.rental_id END);
    RETURN NULL;
END $$;

CREATE OR REPLACE TRIGGER record_change
    AFTER INSERT OR UPDATE OR DELETE ON equipment.equipment
    FOR EACH ROW EXECUTE FUNCTION equipment.record_change('equipment', 'organization_id', 'id');

CREATE OR REPLACE TRIGGER record_change
    AFTER INSERT OR UPDATE OR DELETE ON equipment.rentals
    FOR EACH ROW EXECUTE FUNCTION equipment.record_change(
        'rental', 'organization_id', 'id', '', 'worker_user_id');

CREATE OR REPLACE TRIGGER record_change
    AFTER INSERT OR UPDATE OR DELETE ON equipment.cost_accruals
    FOR EACH ROW EXECUTE FUNCTION equipment.record_accrual_change();
