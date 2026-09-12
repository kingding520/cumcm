function test_make_config()
%TEST_MAKE_CONFIG 配置层的最小可复现校验。

projectRoot = fileparts(fileparts(fileparts(mfilename('fullpath'))));
addpath(fullfile(projectRoot, 'matlab'));
cfg = make_config();

assert(isfile(cfg.paths.attachment1), '附件1路径无效。');
assert(isfile(cfg.paths.attachment2), '附件2路径无效。');
assert(cfg.geometry.radius0 == 0.02, '初始半径单位或数值错误。');
assert(cfg.initial.T == 28.0 && cfg.initial.C == 2.55, '初始条件错误。');
assert(strcmp(cfg.units.temperatureInArrhenius, 'K'), 'Arrhenius 温度单位必须为 K。');
assert(cfg.properties.q1.D(2.55, 301.15) > 0, '问题1扩散系数应为正。');
assert(cfg.properties.q23.D(2.55, 301.15) > 0, '问题2/3扩散系数应为正。');
assert(cfg.properties.q4.D(2.55, 301.15) > 0, '问题4扩散系数应为正。');
assert(strcmp(cfg.stop.eventFunction, 'max(C(:)) - cfg.stop.maxMoisture'), ...
    '问题3必须按全域最大含水率定位事件。');
assert(strcmp(cfg.movingDomain.coordinate, 'material_xi'), ...
    '问题4必须使用材料坐标的移动边界框架。');
assert(isnan(cfg.output.movingDomainOutsideValue), ...
    '收缩后域外固定距离应以 NaN 标记，供导出时留空。');

disp('test_make_config: PASS');
end
