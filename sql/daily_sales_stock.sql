-- Current rolling stock, with the same pending-transfer definition as
-- inventory_web_user.sql. No midnight archive or historical backup is read.
WITH stock AS (
 SELECT swm.wms_code,swm.sku_code,SUM(swm.in_stock_quantity) AS realtime_stock,
        MAX(swm.stock_change_time) AS stock_changed_at
 FROM ywzt_ods.scm_warehous_manage swm
 LEFT JOIN ywzt_ods.master_sku sku ON swm.sku_code=sku.sku_code
 LEFT JOIN ywzt_ods.master_wms w ON swm.wms_code=w.wms_code
 WHERE swm.wms_code IN %(warehouse_codes)s
 GROUP BY swm.wms_code,swm.sku_code
), pending_out AS (
 SELECT a.delivery_wms AS wms_code,b.sku_code,SUM(b.request_qty) AS quantity
 FROM ywzt_ods.scm_transfer_order_header a
 JOIN ywzt_ods.scm_transfer_order_detail b ON a.transfer_order_code=b.transfer_order_code
 LEFT JOIN ywzt_ods.master_wms c ON a.delivery_wms=c.wms_code
 LEFT JOIN ywzt_ods.master_wms d ON a.receive_wms=d.wms_code
 WHERE b.status=0 AND a.status=0 AND a.order_status IN ('10','11','12')
   AND c.wms_node=14
   AND (d.wms_node=15 OR (d.wms_node=14 AND d.wms_code='WMS000097'))
   AND a.delivery_wms IN %(warehouse_codes)s
 GROUP BY a.delivery_wms,b.sku_code
), pending_in AS (
 SELECT a.receive_wms AS wms_code,b.sku_code,SUM(b.request_qty) AS quantity
 FROM ywzt_ods.scm_transfer_order_header a
 JOIN ywzt_ods.scm_transfer_order_detail b ON a.transfer_order_code=b.transfer_order_code
 LEFT JOIN ywzt_ods.master_wms c ON a.receive_wms=c.wms_code
 LEFT JOIN ywzt_ods.master_wms d ON a.delivery_wms=d.wms_code
 WHERE b.status=0 AND a.status=0 AND a.order_status IN ('10','11','12','13','14','15')
   AND c.wms_node=14 AND d.wms_node=15
   AND a.receive_wms IN %(warehouse_codes)s
 GROUP BY a.receive_wms,b.sku_code
)
SELECT s.wms_code,s.sku_code,IFNULL(s.realtime_stock,0) AS realtime_stock,
       IFNULL(o.quantity,0) AS pending_out,IFNULL(i.quantity,0) AS pending_in,
       ROUND(IFNULL(s.realtime_stock,0)-IFNULL(o.quantity,0)+IFNULL(i.quantity,0),0) AS rolling_stock,
       DATE_FORMAT(s.stock_changed_at,'%%Y-%%m-%%d %%H:%%i:%%s') AS stock_changed_at
FROM stock s
JOIN ywzt_ods.master_wms w ON s.wms_code=w.wms_code
LEFT JOIN pending_out o ON s.wms_code=o.wms_code AND s.sku_code=o.sku_code
LEFT JOIN pending_in i ON s.wms_code=i.wms_code AND s.sku_code=i.sku_code
WHERE w.wms_type=12 AND w.wms_status<>12
