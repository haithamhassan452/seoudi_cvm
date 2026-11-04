-- CVM Hub MVP — PostgreSQL schema
--
-- The application creates this automatically (app/db.py::init_db creates the
-- same metadata). This file exists so a DBA can review the schema, and so you
-- can set up a free Neon/Supabase database with one paste.
--
-- Identity rule: customers.id is an internal UUID, never exposed.
--                customers.magento_customer_id is the only external key.
--                No personal data lives on the customers table.


CREATE TABLE customers (
	id VARCHAR(36) NOT NULL, 
	magento_customer_id BIGINT NOT NULL, 
	store_view VARCHAR(32) NOT NULL, 
	locale VARCHAR(8) NOT NULL, 
	city VARCHAR(64), 
	country VARCHAR(2) NOT NULL, 
	registered_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	lifecycle VARCHAR(16) NOT NULL, 
	is_vip BOOLEAN NOT NULL, 
	rfm_r INTEGER, 
	rfm_f INTEGER, 
	rfm_m INTEGER, 
	clv_12m_minor BIGINT NOT NULL, 
	churn_prob FLOAT NOT NULL, 
	price_sensitivity FLOAT NOT NULL, 
	discount_dependency FLOAT NOT NULL, 
	predicted_next_purchase_at TIMESTAMP WITH TIME ZONE, 
	consent_email BOOLEAN NOT NULL, 
	consent_push BOOLEAN NOT NULL, 
	consent_sms BOOLEAN NOT NULL, 
	consent_inapp BOOLEAN NOT NULL, 
	suppressed BOOLEAN NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);


CREATE TABLE offers (
	code VARCHAR(32) NOT NULL, 
	name VARCHAR(96) NOT NULL, 
	mechanic VARCHAR(32) NOT NULL, 
	discount_pct FLOAT NOT NULL, 
	margin_cost_pct FLOAT NOT NULL, 
	min_order_minor BIGINT NOT NULL, 
	ladder_rank INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (code)
);


CREATE TABLE sync_runs (
	id SERIAL NOT NULL, 
	job VARCHAR(48) NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(16) NOT NULL, 
	stats JSON NOT NULL, 
	error TEXT, 
	PRIMARY KEY (id)
);


CREATE TABLE sync_state (
	key VARCHAR(64) NOT NULL, 
	value TEXT NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (key)
);


CREATE TABLE behaviour_events (
	id SERIAL NOT NULL, 
	customer_id VARCHAR(36) NOT NULL, 
	name VARCHAR(48) NOT NULL, 
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	props JSON NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE
);


CREATE TABLE customer_facts (
	customer_id VARCHAR(36) NOT NULL, 
	orders_count INTEGER NOT NULL, 
	orders_90d INTEGER NOT NULL, 
	lifetime_revenue_minor BIGINT NOT NULL, 
	aov_minor BIGINT NOT NULL, 
	discount_share FLOAT NOT NULL, 
	first_order_at TIMESTAMP WITH TIME ZONE, 
	last_order_at TIMESTAMP WITH TIME ZONE, 
	days_since_last_order INTEGER, 
	days_since_first_order INTEGER, 
	sessions_30d INTEGER NOT NULL, 
	carts_abandoned_90d INTEGER NOT NULL, 
	views_30d INTEGER NOT NULL, 
	top_category VARCHAR(48), 
	category_mix JSON NOT NULL, 
	messages_7d INTEGER NOT NULL, 
	last_message_at TIMESTAMP WITH TIME ZONE, 
	computed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (customer_id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE
);


CREATE TABLE customer_pii (
	customer_id VARCHAR(36) NOT NULL, 
	name_enc TEXT, 
	email_enc TEXT, 
	phone_enc TEXT, 
	email_bidx VARCHAR(64), 
	phone_bidx VARCHAR(64), 
	key_version INTEGER NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (customer_id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE
);


CREATE TABLE journey_runs (
	id SERIAL NOT NULL, 
	customer_id VARCHAR(36) NOT NULL, 
	journey_key VARCHAR(48) NOT NULL, 
	step_index INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	entered_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	next_run_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	exited_at TIMESTAMP WITH TIME ZONE, 
	exit_reason VARCHAR(48), 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE
);


CREATE TABLE message_log (
	id SERIAL NOT NULL, 
	customer_id VARCHAR(36) NOT NULL, 
	channel VARCHAR(16) NOT NULL, 
	template VARCHAR(64) NOT NULL, 
	journey_key VARCHAR(48), 
	offer_code VARCHAR(32), 
	sent_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	provider_id VARCHAR(96), 
	detail TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE
);


CREATE TABLE promotion_recommendations (
	id SERIAL NOT NULL, 
	customer_id VARCHAR(36) NOT NULL, 
	offer_code VARCHAR(32) NOT NULL, 
	score FLOAT NOT NULL, 
	reason_code VARCHAR(40) NOT NULL, 
	reason_text VARCHAR(160) NOT NULL, 
	promo_code VARCHAR(48), 
	sync_run_id INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE, 
	FOREIGN KEY(offer_code) REFERENCES offers (code), 
	FOREIGN KEY(sync_run_id) REFERENCES sync_runs (id)
);


CREATE TABLE purchases (
	id SERIAL NOT NULL, 
	customer_id VARCHAR(36) NOT NULL, 
	magento_order_id BIGINT NOT NULL, 
	increment_id VARCHAR(32), 
	placed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	grand_total_minor BIGINT NOT NULL, 
	discount_minor BIGINT NOT NULL, 
	items_count INTEGER NOT NULL, 
	categories JSON NOT NULL, 
	skus JSON NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE, 
	UNIQUE (magento_order_id)
);

