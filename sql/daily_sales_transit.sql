-- Transit definition from the supplied SQL, unchanged status/deletion rules.
-- Aggregate owners at warehouse/SKU grain to match the inventory page.
WITH po AS (
 SELECT p.take_delivery_wms AS wms_code,d.sku_code,
   SUM(IFNULL(d.request_quantity,0)) AS quantity
 FROM ywzt_ods.scm_po_header p
 LEFT JOIN ywzt_ods.scm_po_detail d ON p.po_code=d.po_code
 WHERE p.order_status IN ('12','13','14','15','16') AND d.status=0
   AND p.take_delivery_wms IN %(warehouse_codes)s
 GROUP BY p.take_delivery_wms,d.sku_code
), tr AS (
 SELECT p.receive_wms AS wms_code,d.sku_code,
   SUM(IFNULL(d.allocate_qty,0)) AS quantity
 FROM ywzt_ods.scm_transfer_order_header p
 LEFT JOIN ywzt_ods.scm_sales_shipment_allocate_wms d ON p.outbound_code=d.outbound_code
 WHERE p.order_status IN ('13','14','15')
   AND p.receive_wms IN %(warehouse_codes)s
   AND IFNULL(d._fdl_marked_deleted,'') <> 'true'
 GROUP BY p.receive_wms,d.sku_code
), combined AS (
 SELECT wms_code,sku_code,quantity AS purchase_transit,0 AS transfer_transit FROM po
 UNION ALL
 SELECT wms_code,sku_code,0,quantity FROM tr
)
SELECT c.wms_code,c.sku_code,SUM(c.purchase_transit) AS purchase_transit,
 SUM(c.transfer_transit) AS transfer_transit
FROM combined c JOIN ywzt_ods.master_wms w ON c.wms_code=w.wms_code
WHERE w.wms_type=12 AND w.wms_status<>12
GROUP BY c.wms_code,c.sku_code
