// Business owner assignments supplied on 2026-11-12. Exact names only;
// aliases below cover the confirmed current-cloud names in the Excel mapping.
(() => {
  'use strict';
  const groups = {
    '圆圆': ['多多买菜-赣州云仓', '多多买菜-湖北荆州云仓', '多多买菜-湖北襄阳云仓', '多多买菜-江西南昌云仓', '多多买菜-上饶云仓', '多多买菜-武汉梁子湖云仓', '多多买菜-新衡阳云仓', '多多买菜-长沙云仓'],
    '杏子': ['多多买菜-大理云仓', '多多买菜-贵州云仓', '多多买菜-南充云仓', '多多买菜-四川成都云仓', '多多买菜-云南云仓', '多多买菜-重庆云仓', '多多买菜-自贡云仓'],
    '辛巴': ['多多买菜-东莞云仓', '多多买菜-佛山云仓', '多多买菜-海南云仓', '多多买菜-揭阳云仓', '多多买菜-茂名云仓', '多多买菜-浙江金华云仓', '多多买菜-浙江宁波云仓', '多多买菜-中山云仓', '多多买菜-惠州云仓'],
    '溪午': ['多多买菜-包头云仓', '多多买菜-赤峰云仓', '多多买菜-吉林长春云仓', '多多买菜-佳木斯云仓', '多多买菜-辽宁大连云仓', '多多买菜-齐齐哈尔云仓', '多多买菜-山西侯马云仓', '多多买菜-沈阳云仓', '多多买菜-新哈尔滨云仓', '多多买菜-新呼和浩特云仓'],
    '晚渡': ['多多买菜-甘肃云仓', '多多买菜-江应西安云仓', '多多买菜-银川云仓', '多多买菜-郑州云仓', '多多买菜-驻马店云仓'],
    '满汐': ['多多买菜-安徽芜湖云仓', '多多买菜-杭州云仓', '多多买菜-合肥云仓', '多多买菜-无锡云仓', '多多买菜-新淮安云仓', '多多买菜-新上海云仓', '多多买菜-浙江温州云仓（多多）', '社区团购南京顺丰仓'],
    '杰西': ['多多买菜-北京云仓', '多多买菜-河北保定云仓', '多多买菜-河北石家庄云仓', '多多买菜-山西晋中云仓', '多多买菜-天津云仓', '多多买菜-新河北唐山云仓', '多多买菜-新山西大同云仓', '多多买菜-榆林云仓'],
    '存韫': ['多多买菜—新烟台云仓', '多多买菜-山东济宁云仓', '多多买菜-新济南云仓', '多多买菜-新青岛云仓', '多多买菜-徐州云仓'],
  };
  const normalize = name => String(name || '').trim().replace(/[—－]/g, '-');
  const assignments = new Map();
  for (const [owner, names] of Object.entries(groups)) for (const name of names) assignments.set(normalize(name), owner);
  const aliases = {
    '多多买菜-新东莞云仓': '多多买菜-东莞云仓',
    '多多买菜-新茂名云仓': '多多买菜-茂名云仓',
    '多多买菜-新杭州云仓': '多多买菜-杭州云仓',
    '多多买菜-新天津云仓': '多多买菜-天津云仓',
    '多多买菜-昆明云仓': '多多买菜-云南云仓',
  };
  function forCloud(name) {
    const key = normalize(name);
    return assignments.get(aliases[key] || key) || '待分配';
  }
  function forRow(row) {
    const names = row.cloud_warehouse_names?.length ? row.cloud_warehouse_names : [row.cloud_warehouse_name];
    return [...new Set(names.map(forCloud))].join('、');
  }
  const api = Object.freeze({ names: Object.freeze([...Object.keys(groups), '待分配']), forCloud, forRow });
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.dailySalesOwners = api;
})();
