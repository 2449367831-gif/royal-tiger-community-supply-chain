SELECT
    stock.wms_code AS "仓库编码",
    mwms.wms_desc AS "仓库名称",
    stock.sku_code AS "SKU编码",
    COALESCE(sku.sku_name, stock.sku_name) AS "SKU名称",
    ROUND(stock.in_stock_quantity, 0) AS "当日库存",
    NULL AS "期间出库数量",
    NULL AS "期间退货数量",
    NULL AS "净数量",
    NULL AS "统计天数",
    NULL AS "每日消耗",
    NULL AS "预计天数",
    stock.in_stock_quantity AS "实时库存",
    DATE_FORMAT(stock.stock_change_time, '%Y-%m-%d %H:%i:%s') AS "库存变动时间",
    NULL AS "待出库",
    NULL AS "待入库"
FROM (
    SELECT wms_code, sku_code, MAX(sku_name) AS sku_name,
           SUM(IFNULL(in_stock_quantity, 0)) AS in_stock_quantity,
           MAX(stock_change_time) AS stock_change_time
    FROM scm_warehous_manage_backups
    WHERE backups_date = [DYNAMIC_PARAMS.stock_date]
      AND (sku_code IN ([DYNAMIC_PARAMS.SKU编码]) OR '' IN ([DYNAMIC_PARAMS.SKU编码]))
      AND (wms_code IN ([DYNAMIC_PARAMS.仓库编码]) OR '' IN ([DYNAMIC_PARAMS.仓库编码]))
    GROUP BY wms_code, sku_code
) stock
LEFT JOIN master_wms mwms ON mwms.wms_code = stock.wms_code
LEFT JOIN master_sku sku ON sku.sku_code = stock.sku_code
WHERE mwms.wms_type = 12 AND mwms.wms_status <> 12
