function cfg = make_config()
%MAKE_CONFIG 唯一的建模配置入口。
% 所有问题共享同一圆柱径向热-质传递框架；求解器不得在内部硬编码本文件已有参数。

thisFile = mfilename('fullpath');
matlabDir = fileparts(thisFile);
rootDir = fileparts(matlabDir);

cfg.paths.root = rootDir;
cfg.paths.attachment1 = fullfile(rootDir, '附件', '附件1.xlsx');
cfg.paths.attachment2 = fullfile(rootDir, '附件', '附件2.xlsx');
cfg.paths.template1 = fullfile(rootDir, '附件', '附件3', 'result1.xlsx');
cfg.paths.template2 = fullfile(rootDir, '附件', '附件3', 'result2.xlsx');
cfg.paths.template3 = fullfile(rootDir, '附件', '附件3', 'result3.xlsx');
cfg.paths.template4 = fullfile(rootDir, '附件', '附件3', 'result4.xlsx');
cfg.paths.outputDir = fullfile(rootDir, 'outputs');

requiredFiles = {cfg.paths.attachment1, cfg.paths.attachment2, ...
    cfg.paths.template1, cfg.paths.template2, cfg.paths.template3, cfg.paths.template4};
assert(all(cellfun(@isfile, requiredFiles)), ...
    '缺少题目附件或结果模板；请确认工程根目录未改变。');

% SI 制内部计算；温度状态保存为摄氏度，经验扩散式只接受开尔文温度。
cfg.units.length = 'm';
cfg.units.time = 's';
cfg.units.temperatureState = 'degC';
cfg.units.temperatureInArrhenius = 'K';
cfg.units.moisture = 'kg/kg (dry basis)';
cfg.conversion.cmToM = 1e-2;
cfg.conversion.hourToS = 3600;
cfg.conversion.celsiusToKelvin = 273.15;

% 几何与初始条件。
cfg.geometry.length = 0.25;
cfg.geometry.radius0 = 0.02;
cfg.initial.T = 28.0;
cfg.initial.C = 2.55;

% r 轴正方向取从中心指向表面。表面向外热/质通量分别为
% qOut=-k*dT/dr 与 jOut=-D*dC/dr。
cfg.boundary.h = 25.0;
cfg.boundary.hm = 8e-7;
cfg.boundary.interpolation = 'linear';
cfg.boundary.lastObservedTime = 14400;
cfg.boundary.postObservation.mode = 'nominal_setpoint';
cfg.boundary.postObservation.Tinf = 50.0;
cfg.boundary.postObservation.Cinf = 0.05;
cfg.boundary.postObservation.alternatives = {'last_observation', 'last_30min_mean'};

% 问题1：附录2。
cfg.properties.q1.rho = @(C) 820.0 + zeros(size(C));
cfg.properties.q1.cp = @(C) 2600.0 + zeros(size(C));
cfg.properties.q1.k = @(C) 0.36 + zeros(size(C));
cfg.properties.q1.D = @(C, TK) 7e-9 .* exp(-0.89 ./ C);

% 问题2和问题3：附录3。TK 必须是开尔文，求解器在调用前负责转换。
cfg.properties.q23.rho = @(C) 650.0 + 128.0 .* C;
cfg.properties.q23.cp = @(C) 1450.0 + 2736.0 .* C ./ (C + 1.0);
cfg.properties.q23.k = @(C) 0.21 + 0.38 .* C ./ (C + 1.0);
cfg.properties.q23.D = @(C, TK) 2.4e-3 .* exp(-0.45 ./ C) .* exp(-3850.0 ./ TK);

% 问题4：附录4。
cfg.properties.q4.rho = @(C) 760.0 + 90.0 .* C;
cfg.properties.q4.cp = @(C) 1850.0 + 2150.0 .* C ./ (C + 1.0);
cfg.properties.q4.k = @(C) 0.12 + 0.20 .* C ./ (C + 1.0);
cfg.properties.q4.D = @(C, TK) 4.2e-4 .* exp(-0.30 ./ C) .* exp(-3850.0 ./ TK);

% 输出要求。outputRadiusCm 为固定物理距离；问题4超出当前 R(t) 的位置输出 NaN，
% 导出 Excel 时写成空白；surface 列则始终取 xi=1 的真实表面解。
cfg.output.radiusCm = 0.0:0.1:2.0;
cfg.output.radiusM = cfg.output.radiusCm .* cfg.conversion.cmToM;
cfg.output.q1Times = 1:1800;
cfg.output.q2Times = 1:10800;
cfg.output.q3q4Step = 60;
cfg.output.roundDigits = 4;
cfg.output.movingDomainOutsideValue = NaN;
cfg.output.movingDomainSurfaceName = '药材表面';

% 数值求解：正式空间网格与收敛网格分离，不能把 0.1 cm 输出点当作唯一计算网格。
cfg.numerics.method = 'radial_finite_volume_method_of_lines_ode15s';
cfg.numerics.activeCells = 160;
cfg.numerics.convergenceCells = [80, 160, 320];
cfg.numerics.relTol = 1e-7;
cfg.numerics.absTolTemperature = 1e-7;
cfg.numerics.absTolMoisture = 1e-9;
cfg.numerics.maxInternalStep = 10;
cfg.numerics.nonlinearFaceAverage = 'harmonic_for_D_and_k';

% 判停和验证门槛。阈值判断全程使用未舍入值。
cfg.stop.maxMoisture = 0.15;
cfg.stop.eventFunction = 'max(C(:)) - cfg.stop.maxMoisture';
cfg.stop.rootTolerance = 1e-9;
cfg.validation.temperatureGridTolerance = 5e-4;
cfg.validation.moistureGridTolerance = 5e-5;
cfg.validation.endTimeTolerance = 60;
cfg.validation.requireMassBalance = true;
cfg.validation.requireFixedRadiusReductionForQ4 = true;

% 问题4采用材料坐标 xi=r/R(t)；半径数据须保形单调插值。
cfg.movingDomain.coordinate = 'material_xi';
cfg.movingDomain.radiusInterpolation = 'pchip_monotone';
cfg.movingDomain.radiusAfterLastObservation = 'hold_last_value';
cfg.movingDomain.xiCells = cfg.numerics.activeCells;

end
