--
-- 归档对账批次与对账项
-- 依据回执冻结来源重算延误统计并校验共享产物卷，区分缺档/坏档/串档/来源自相矛盾/孤立文件。
--

CREATE TABLE public.recon_batches (
    id integer NOT NULL,
    state character varying(20) NOT NULL,
    created_at character varying(40) NOT NULL,
    started_at character varying(40),
    finished_at character varying(40),
    totals json,
    note text
);

CREATE SEQUENCE public.recon_batches_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.recon_batches_id_seq OWNED BY public.recon_batches.id;

CREATE TABLE public.recon_items (
    id integer NOT NULL,
    batch_id integer NOT NULL,
    receipt_id integer,
    artifact_name character varying(200),
    finding character varying(20),
    disposition character varying(20) NOT NULL,
    evidence json,
    created_at character varying(40) NOT NULL,
    updated_at character varying(40) NOT NULL
);

CREATE SEQUENCE public.recon_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.recon_items_id_seq OWNED BY public.recon_items.id;

ALTER TABLE ONLY public.recon_batches ALTER COLUMN id SET DEFAULT nextval('public.recon_batches_id_seq'::regclass);
ALTER TABLE ONLY public.recon_items ALTER COLUMN id SET DEFAULT nextval('public.recon_items_id_seq'::regclass);

ALTER TABLE ONLY public.recon_batches
    ADD CONSTRAINT recon_batches_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.recon_items
    ADD CONSTRAINT recon_items_pkey PRIMARY KEY (id);

-- 同批次同回执/同文件名只保留一条差异；孤立文件 receipt_id 为 NULL。
ALTER TABLE ONLY public.recon_items
    ADD CONSTRAINT uq_recon_item UNIQUE (batch_id, receipt_id, artifact_name);

ALTER TABLE ONLY public.recon_items
    ADD CONSTRAINT recon_items_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.recon_batches(id);

ALTER TABLE ONLY public.recon_items
    ADD CONSTRAINT recon_items_receipt_id_fkey FOREIGN KEY (receipt_id) REFERENCES public.receipts(id);

-- 同一时刻只允许一个未结批次（pending/scanning/held），completed 不受此限。
CREATE UNIQUE INDEX uq_recon_open_state ON public.recon_batches (state)
    WHERE state IN ('pending', 'scanning', 'held');

CREATE INDEX ix_recon_items_batch_id ON public.recon_items (batch_id);
