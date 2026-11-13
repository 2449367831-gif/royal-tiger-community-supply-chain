WITH warehouse_groups AS (
    SELECT key AS warehouse_name,value AS city FROM jsonb_each_text(%(warehouse_cities)s::jsonb)
), __SKU_MAPPING_CTES__, forecast_latest AS (
    -- Deduplicate repeated price tiers at the ORIGINAL warehouse/product grain.
    -- Different numbered warehouses must still contribute separate quantities.
    SELECT DISTINCT ON (account_name, warehouse_name, product_id)
        account_name, warehouse_name, warehouse_id, product_id, product_name,
        sell_unit_name, estimated_total_sales, estimated_updated_at
    FROM public.pdd_order_today
    WHERE sale_date = %(day)s
        AND (%(capture_day)s::date IS NULL OR captured_date = %(capture_day)s::date)
    ORDER BY account_name, warehouse_name, product_id,
        estimated_updated_at DESC NULLS LAST, captured_date DESC NULLS LAST, id DESC
), forecast AS (
    SELECT g.city AS warehouse_name, m.sku_code, MAX(m.sku_name) AS product_name,
        STRING_AGG(DISTINCT f.warehouse_id, '、' ORDER BY f.warehouse_id) AS warehouse_id,
        STRING_AGG(DISTINCT f.account_name, '、' ORDER BY f.account_name) AS accounts,
        CASE WHEN COUNT(f.estimated_total_sales) = COUNT(*) THEN SUM(f.estimated_total_sales * m.ratio) END AS estimated_sales,
        MAX(f.estimated_updated_at) AS estimated_updated_at
    FROM forecast_latest f JOIN mapping m ON m.product_id = f.product_id
    JOIN warehouse_groups g ON g.warehouse_name=f.warehouse_name
    GROUP BY g.city,m.sku_code
), mapped_sales AS MATERIALIZED (
    SELECT g.city AS warehouse_name,s.warehouse_name AS original_warehouse,
        m.sku_code,m.sku_name AS product_name,s.product_id,s.sale_date,
        s.sell_unit_total*m.ratio AS quantity
    FROM public.pdd_sales_data s JOIN mapping m ON m.product_id=s.product_id
    JOIN warehouse_groups g ON g.warehouse_name=s.warehouse_name
    WHERE s.sale_date >= %(day)s::date - 7 AND s.sale_date < %(day)s::date
        AND (s.error_message IS NULL OR s.error_message = '') AND s.sell_unit_total IS NOT NULL
), daily AS (
    SELECT warehouse_name,sku_code,sale_date,MAX(product_name) AS product_name,SUM(quantity) AS quantity
    FROM mapped_sales GROUP BY warehouse_name,sku_code,sale_date
), history AS (
    SELECT warehouse_name, sku_code,
        MAX(product_name) AS product_name, COUNT(*) AS valid_days,
        SUM(quantity) AS total_sales, MAX(quantity) AS peak_sales,
        CASE WHEN COUNT(*) = 7 THEN SUM(quantity) / 7 ELSE MAX(quantity) END AS reference_sales
    FROM daily GROUP BY warehouse_name, sku_code
), source_products AS (
    SELECT g.city AS warehouse_name,f.warehouse_name AS original_warehouse,m.sku_code,f.product_id
    FROM forecast_latest f JOIN mapping m ON m.product_id=f.product_id
    JOIN warehouse_groups g ON g.warehouse_name=f.warehouse_name
    UNION
    SELECT warehouse_name,original_warehouse,sku_code,product_id FROM mapped_sales
), sources AS (
    SELECT warehouse_name,sku_code,
        STRING_AGG(DISTINCT product_id, '、' ORDER BY product_id) AS product_id,
        COUNT(DISTINCT product_id) AS platform_sku_count,
        STRING_AGG(DISTINCT original_warehouse, '、' ORDER BY original_warehouse) AS source_warehouses,
        COUNT(DISTINCT original_warehouse) AS platform_warehouse_count
    FROM source_products GROUP BY warehouse_name,sku_code
)
SELECT COALESCE(f.warehouse_name,h.warehouse_name) AS warehouse_name,
    f.warehouse_id,p.product_id,p.platform_sku_count,p.source_warehouses,p.platform_warehouse_count,
    COALESCE(f.sku_code,h.sku_code) AS sku_code,
    COALESCE(f.product_name,h.product_name) AS product_name,
    '映射基础单位' AS unit,f.accounts,
    f.estimated_sales,f.estimated_updated_at,
    COALESCE(h.valid_days,0) AS valid_days,
    h.total_sales,h.peak_sales,h.reference_sales,
    CASE WHEN h.valid_days=7 THEN 'average' WHEN h.valid_days>0 THEN 'peak' ELSE 'unavailable' END AS reference_kind
FROM forecast f FULL OUTER JOIN history h
    ON f.warehouse_name=h.warehouse_name AND f.sku_code=h.sku_code
JOIN sources p ON p.warehouse_name=COALESCE(f.warehouse_name,h.warehouse_name) AND p.sku_code=COALESCE(f.sku_code,h.sku_code)
ORDER BY COALESCE(f.warehouse_name,h.warehouse_name),COALESCE(f.sku_code,h.sku_code)
LIMIT 20001
