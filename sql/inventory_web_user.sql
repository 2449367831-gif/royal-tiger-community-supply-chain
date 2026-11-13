SELECT
	wms.wms_code "仓库编码",
	mwms.wms_desc "仓库名称",
	wms.sku_code "SKU编码",
	sku.sku_name "SKU名称",
	round(ifnull(wms.in_stock_quantity,0)-ifnull(tout_v.out_qty_v,0)+ifnull(tin_v.in_qty_v,0),0)  "当日库存",

	round(tout.out_qty,0) "期间出库数量",
	round(tin.in_qty,0) "期间退货数量",
	round(tout.out_qty - tin.in_qty,0) "净数量",
	IF('' = [DYNAMIC_PARAMS.t1],0 ,DATEDIFF( [DYNAMIC_PARAMS.t2] , [DYNAMIC_PARAMS.t1] )+1) AS "统计天数",
	round(
		( ifnull( tout.out_qty, 0 ) - ifnull( tin.in_qty, 0 ) ) / ( IF('' = [DYNAMIC_PARAMS.t1],0 ,DATEDIFF( [DYNAMIC_PARAMS.t2] , [DYNAMIC_PARAMS.t1] )+1) ),
		0
	) AS "每日消耗",
	abs(
		round(
			round(ifnull(wms.in_stock_quantity,0)-ifnull(tout_v.out_qty_v,0)+ifnull(tin_v.in_qty_v,0),0)/ round(
				( ifnull( tout.out_qty, 0 ) - ifnull( tin.in_qty, 0 ) ) / ( IF('' = [DYNAMIC_PARAMS.t1],0 ,DATEDIFF( [DYNAMIC_PARAMS.t2] , [DYNAMIC_PARAMS.t1] )+1)),
				2
			),
			0
		)) AS "预计天数" ,
	ifnull(wms.in_stock_quantity,0) "实时库存",
	DATE_FORMAT(wms.max_stock_change_time, '%Y-%m-%d %H:%i:%s') AS "库存变动时间",
	ifnull(tout_v.out_qty_v,0) as "待出库",
	ifnull(tin_v.in_qty_v,0) as "待入库"
FROM
	(
	SELECT
		swm.wms_code,
		swm.sku_code,
		MAX(swm.stock_change_time) AS max_stock_change_time,
		SUM(swm.in_stock_quantity) AS in_stock_quantity
	FROM
		scm_warehous_manage swm
		left join master_sku on swm.sku_code=master_sku.sku_code
		left join master_wms wms on swm.wms_code=wms.wms_code
	WHERE
		1=1
		AND (swm.sku_code in ( [DYNAMIC_PARAMS.SKU编码] ) or '' in ( [DYNAMIC_PARAMS.SKU编码] ))
		AND (swm.wms_code in ( [DYNAMIC_PARAMS.仓库编码] ) or '' in ( [DYNAMIC_PARAMS.仓库编码] ))
	GROUP BY
		swm.wms_code,
		swm.sku_code
	) wms
	LEFT JOIN (
	SELECT
		c.wms_code,
		b.sku_code,
		sum( b.request_qty ) out_qty
	FROM
		scm_transfer_order_header a
		INNER JOIN scm_transfer_order_detail b ON a.transfer_order_code = b.transfer_order_code
		LEFT JOIN master_wms c ON a.delivery_wms = c.wms_code
		LEFT JOIN master_wms d ON a.receive_wms = d.wms_code
	WHERE
		 b.`status` = 0
     and a.status=0
		AND c.wms_node = 14
		AND d.wms_node = 15
		AND (b.sku_code in ( [DYNAMIC_PARAMS.SKU编码] ) or '' in ( [DYNAMIC_PARAMS.SKU编码] ))
		AND (c.wms_code in ( [DYNAMIC_PARAMS.仓库编码] ) or '' in ( [DYNAMIC_PARAMS.仓库编码] ))
		AND (a.cloud_wms_order_date between  [DYNAMIC_PARAMS.t1]  and  [DYNAMIC_PARAMS.t2] or '' = [DYNAMIC_PARAMS.t1])
	GROUP BY
		c.wms_code,
		b.sku_code
	) tout ON tout.wms_code = wms.wms_code
	AND tout.sku_code = wms.sku_code
	LEFT JOIN (
	SELECT
		c.wms_code,
		b.sku_code,
		sum( b.request_qty ) in_qty
	FROM
		scm_transfer_order_header a
		INNER JOIN scm_transfer_order_detail b ON a.transfer_order_code = b.transfer_order_code
		AND b.STATUS = 0
		LEFT JOIN master_wms c ON a.receive_wms = c.wms_code
		LEFT JOIN master_wms d ON a.delivery_wms = d.wms_code
	WHERE
		c.wms_node = 14
		and b.status=0
    and a.status=0
		AND d.wms_node = 15
		AND (b.sku_code in ( [DYNAMIC_PARAMS.SKU编码] ) or '' in ( [DYNAMIC_PARAMS.SKU编码] ))
		AND (c.wms_code in ( [DYNAMIC_PARAMS.仓库编码] ) or '' in ( [DYNAMIC_PARAMS.仓库编码] ))
		AND (a.cloud_wms_order_date between  [DYNAMIC_PARAMS.t1]  and  [DYNAMIC_PARAMS.t2] or '' = [DYNAMIC_PARAMS.t1])
	GROUP BY
		c.wms_code,
		b.sku_code
	) tin ON tin.wms_code = wms.wms_code AND tin.sku_code = wms.sku_code
	LEFT JOIN (
	SELECT
		c.wms_code,
		b.sku_code,
		sum( b.request_qty ) out_qty_V
	FROM
		scm_transfer_order_header a
		INNER JOIN scm_transfer_order_detail b ON a.transfer_order_code = b.transfer_order_code
		LEFT JOIN master_wms c ON a.delivery_wms = c.wms_code
		LEFT JOIN master_wms d ON a.receive_wms = d.wms_code
	WHERE
		 b.`status` = 0
     and a.order_status in ("10","11","12")
		 and a.status=0
		AND c.wms_node = 14
                AND ( (d.wms_node = 15)
                 OR (d.wms_node = 14 AND d.wms_code = 'WMS000097') )
		AND (b.sku_code in ( [DYNAMIC_PARAMS.SKU编码] ) or '' in ( [DYNAMIC_PARAMS.SKU编码] ))
		AND (c.wms_code in ( [DYNAMIC_PARAMS.仓库编码] ) or '' in ( [DYNAMIC_PARAMS.仓库编码] ))
	GROUP BY
		c.wms_code,
		b.sku_code
	) tout_v ON tout_v.wms_code = wms.wms_code  AND tout_v.sku_code = wms.sku_code
	LEFT JOIN (
	SELECT
		c.wms_code,
		b.sku_code,
		sum( b.request_qty ) in_qty_v
	FROM
		scm_transfer_order_header a
		INNER JOIN scm_transfer_order_detail b ON a.transfer_order_code = b.transfer_order_code
		AND b.STATUS = 0
		LEFT JOIN master_wms c ON a.receive_wms = c.wms_code
		LEFT JOIN master_wms d ON a.delivery_wms = d.wms_code
	WHERE
		c.wms_node = 14
		and a.order_status in ("10","11","12","13","14",'15')
		and b.status=0
    and a.status=0
		AND d.wms_node = 15
		AND (b.sku_code in ( [DYNAMIC_PARAMS.SKU编码] ) or '' in ( [DYNAMIC_PARAMS.SKU编码] ))
		AND (c.wms_code in ( [DYNAMIC_PARAMS.仓库编码] ) or '' in ( [DYNAMIC_PARAMS.仓库编码] ))
	GROUP BY
		c.wms_code,
		b.sku_code
	) tin_v ON tin_v.wms_code = wms.wms_code AND tin_v.sku_code = wms.sku_code
	LEFT JOIN master_wms mwms ON mwms.wms_code = wms.wms_code
	LEFT JOIN master_sku sku ON sku.sku_code = wms.sku_code
where mwms.wms_type=12
        AND mwms.wms_status <> 12