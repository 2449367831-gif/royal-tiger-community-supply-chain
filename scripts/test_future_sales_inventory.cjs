const {test} = require('node:test');
const assert = require('node:assert/strict');
const {attachInventory} = require('../future_sales_inventory');

const today = '2026-11-13';
function inputs(rolling = 200, transit = 300) {
  return {
    payload: {synthetic:false, training_cutoff:'2026-11-12', rows:Array.from({length:7}, (_, i) => ({
      sku:'惠州::SKU-1', warehouse_name:'惠州', horizon:i+1,
      target_date:`2026-11-${13+i}`, ensemble_pred:100,
    }))},
    snapshot: {ok:true, snapshot:{}, alerts:{stock_basis:'rolling-adjusted-with-transit-v1',
      stock_date:today, stock_status:'available', transit_status:'available'}, rows:[{
      warehouse_name:'惠州', sku_code:'SKU-1', cloud_warehouse_code:'WMS000339',
      rolling_stock:rolling, transit_stock:transit,
      purchase_transit:transit * .6, transfer_transit:transit * .4,
    }]},
  };
}
function stock(fixture) { return attachInventory(fixture.payload, fixture.snapshot, today).rows[0].inventory; }

test('current purchase and transfer transit reduce the full seven-day gap', () => {
  const f=inputs(), result=attachInventory(f.payload, f.snapshot, today);
  assert.equal(result.rows[0].inventory.cloud_stock,500);
  assert.equal(result.rows[0].inventory.purchase_transit,180);
  assert.equal(result.rows[0].inventory.transfer_transit,120);
  assert.equal(result.rows[0].inventory.seven_day_stock_gap,200);
  assert.ok(result.rows.every(r=>r.inventory===result.rows[0].inventory));
  assert.match(result.inventory.basis,/计入当前全部在途/);
});
test('transit can close a gap, and a surplus never produces a negative gap', () => {
  assert.equal(stock(inputs(200,500)).seven_day_stock_gap,0);
  assert.equal(stock(inputs(200,800)).seven_day_stock_gap,0);
  assert.equal(stock(inputs(200,0)).seven_day_stock_gap,500);
});
test('negative rolling stock is preserved and demand is not rounded before subtraction', () => {
  const f=inputs(-100,50);f.payload.rows.forEach(r=>r.ensemble_pred=100.25);
  assert.equal(stock(f).seven_day_stock_gap,751.75);
});
test('unknown transit or rolling stock cannot be treated as zero', () => {
  for (const [rolling,transit] of [[200,null],[null,300]]) {
    assert.equal(stock(inputs(rolling,transit)).seven_day_stock_gap,null);
  }
  const f=inputs();f.snapshot.alerts.transit_status='unavailable';
  assert.equal(stock(f).seven_day_stock_gap,null);
});
test('stale inventory, stale forecasts, and failed refresh snapshots still suppress gaps', () => {
  const a=inputs();a.snapshot.alerts.stock_date='2026-11-12';
  const b=inputs();b.payload.training_cutoff='2026-11-11';
  const c=inputs();c.snapshot.snapshot.last_error='refresh failed';
  for(const f of [a,b,c]) assert.equal(stock(f).seven_day_stock_gap,null);
});
test('partial, missing, or duplicate forecast days do not create a seven-day gap', () => {
  const a=inputs();a.payload.rows.pop();
  const b=inputs();b.payload.rows[0].ensemble_pred=null;
  const c=inputs();c.payload.rows[0].target_date=c.payload.rows[1].target_date;
  for(const f of [a,b,c]) assert.equal(stock(f).seven_day_stock_gap,null);
});
test('unmatched and ambiguous warehouse/SKU inventory remain unavailable', () => {
  const a=inputs();a.snapshot.rows=[];
  const b=inputs();b.snapshot.rows.push({...b.snapshot.rows[0]});
  for(const f of [a,b]) assert.equal(stock(f).seven_day_stock_gap,null);
});
