-- Independent copy initialized on 2026-11-11; n8n only adds missing mappings here.
-- Resolve the latest record BEFORE validation; never fall back to an old mapping.
mapping_latest AS (
    SELECT DISTINCT ON ("platform_skuId")
        "platform_skuId" AS product_id, sku_code, sku_name, COALESCE(ratio, 1) AS ratio
    FROM public.community_plan_sku_mapping
    ORDER BY "platform_skuId", updated_date DESC NULLS LAST, id DESC
), bundle_expected(platform_id, sku_code) AS (
    VALUES ('867646793186', 'QY-HJKC508G'), ('867646598972', 'QY-YWKC508G')
), bundle_verified AS (
    SELECT m.* FROM mapping_latest m JOIN bundle_expected e
        ON m.product_id=e.platform_id AND m.sku_code=e.sku_code
    WHERE m.ratio=1 AND NULLIF(BTRIM(m.sku_name), '') IS NOT NULL
), mapping AS (
    SELECT product_id, sku_code, sku_name, ratio FROM mapping_latest
    WHERE product_id <> '1013779478701'
        AND NULLIF(BTRIM(sku_code), '') IS NOT NULL AND ratio > 0
    UNION ALL
    -- Each bundle consumes one of EACH component, not half a component and
    -- not the synthetic parent SKU. Require both verified components.
    SELECT '1013779478701', sku_code, sku_name, ratio FROM bundle_verified
    WHERE (SELECT COUNT(*) FROM bundle_verified)=2
)
