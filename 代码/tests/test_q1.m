function report = test_q1(varargin)
projectRoot = fileparts(fileparts(fileparts(mfilename('fullpath'))));
addpath(fullfile(projectRoot, 'matlab'));
cfg = make_config();

p = inputParser;
addParameter(p, 'runConvergence', true, @(x) isscalar(x) && (islogical(x) || isnumeric(x)));
parse(p, varargin{:});
runConvergence = logical(p.Results.runConvergence);

assert(strcmp(cfg.units.length, 'm') && strcmp(cfg.units.time, 's'), ...
    '内部长度和时间必须使用 SI 单位。');
assert(strcmp(cfg.units.temperatureInArrhenius, 'K'), ...
    'Arrhenius 温度必须使用 K。');
assert(cfg.geometry.radius0 > 0 && cfg.geometry.length > 0, '几何尺寸必须为正。');
assert(cfg.boundary.h > 0 && cfg.boundary.hm > 0, 'Robin 边界传递系数必须为正。');
assert(cfg.properties.q1.D(2.55, 301.15) > 0, '问题1扩散系数必须为正。');

out = solve_q1(cfg, 'nCells', 160, 'tEval', [0, 60, 600, 1200, 1800]);
assert(size(out.T, 1) == numel(out.t) && size(out.T, 2) == 160, ...
    '温度结果维度错误。');
assert(isequal(size(out.T), size(out.C)), '温度和水分结果维度必须一致。');
assert(all(isfinite(out.T(:))) && all(isfinite(out.C(:))), '结果不得包含 NaN/Inf。');
assert(min(out.C(:)) >= -1e-10, '水分浓度出现负值。');

afterInitial = 2:numel(out.t);
temperatureGap = out.T(afterInitial, end) - out.T(afterInitial, 1);
moistureGap = out.C(afterInitial, end) - out.C(afterInitial, 1);
assert(min(temperatureGap) >= -1e-8, '温度方向错误：表面侧不应长期低于中心侧。');
assert(max(moistureGap) <= 1e-8, '水分方向错误：表面侧不应长期高于中心侧。');
surfaceSample = sample_final(out, cfg, [0, out.radius]);
assert(surfaceSample.T(2) >= surfaceSample.T(1) - 1e-8, ...
    'Robin 重构后的真实表面温度不应低于中心温度。');
assert(surfaceSample.C(2) <= surfaceSample.C(1) + 1e-8, ...
    'Robin 重构后的真实表面含水率不应高于中心含水率。');

equilibrium = struct('t', [0; 300], 'Tinf', [28; 28], 'Cinf', [2.55; 2.55]);
eq = solve_q1(cfg, 'nCells', 40, 'tEval', [0, 30, 150, 300], ...
    'environment', equilibrium);
assert(max(abs(eq.T(:) - cfg.initial.T)) < 1e-8, ...
    '恒定平衡简化算例的温度未保持不变。');
assert(max(abs(eq.C(:) - cfg.initial.C)) < 1e-8, ...
    '恒定平衡简化算例的水分未保持不变。');

report = struct();
report.physical = struct('minMoisture', min(out.C(:)), ...
    'minSurfaceCentreTemperatureGap', min(temperatureGap), ...
    'maxSurfaceCentreMoistureGap', max(moistureGap), ...
    'finalSurfaceCentreTemperatureGap', surfaceSample.T(2) - surfaceSample.T(1), ...
    'finalSurfaceCentreMoistureGap', surfaceSample.C(2) - surfaceSample.C(1));
report.equilibrium = struct('maxTemperatureError', max(abs(eq.T(:) - cfg.initial.T)), ...
    'maxMoistureError', max(abs(eq.C(:) - cfg.initial.C)));

if runConvergence
    cells = [80, 160, 320];
    convergence = repmat(struct('nCells', 0, 'out', []), numel(cells), 1);
    for k = 1:numel(cells)
        convergence(k).nCells = cells(k);
        convergence(k).out = solve_q1(cfg, 'nCells', cells(k), ...
            'tEval', [0, 600, 1200, 1800]);
    end
    targetRadius = convergence(2).out.r(:).';
    final80 = sample_final(convergence(1).out, cfg, targetRadius);
    final160 = sample_final(convergence(2).out, cfg, targetRadius);
    final320 = sample_final(convergence(3).out, cfg, targetRadius);
    temperatureError = max(abs(final160.T - final320.T));
    moistureError = max(abs(final160.C - final320.C));
    assert(temperatureError <= cfg.validation.temperatureGridTolerance, ...
        '80/160/320 网格温度收敛未达到配置阈值：%.6g。', temperatureError);
    assert(moistureError <= cfg.validation.moistureGridTolerance, ...
        '80/160/320 网格水分收敛未达到配置阈值：%.6g。', moistureError);
    report.convergence = struct('cells', cells, ...
        'temperatureMaxError160vs320', temperatureError, ...
        'moistureMaxError160vs320', moistureError);
else
    report.convergence = struct('skipped', true);
end

disp('test_q1: PASS');
end

function sampled = sample_final(out, cfg, targetRadius)
Trow = out.T(end, :);
Crow = out.C(end, :);
Tinf = interp1(out.environment.t, out.environment.Tinf, out.t(end), 'linear', 'extrap');
Cinf = interp1(out.environment.t, out.environment.Cinf, out.t(end), 'linear', 'extrap');
dr = out.radius / numel(out.r);
kLast = cfg.properties.q1.k(max(Crow(end), 1e-12));
DLast = cfg.properties.q1.D(max(Crow(end), 1e-12), Trow(end) + cfg.conversion.celsiusToKelvin);
qOut = (Trow(end) - Tinf) / (dr / (2 * kLast) + 1 / cfg.boundary.h);
jOut = (Crow(end) - Cinf) / (dr / (2 * DLast) + 1 / cfg.boundary.hm);
Tsurf = Tinf + qOut / cfg.boundary.h;
Csurf = Cinf + jOut / cfg.boundary.hm;
coordinate = [0, out.r(:).', out.radius];
sampled.T = interp1(coordinate, [Trow(1), Trow, Tsurf], targetRadius, 'linear');
sampled.C = interp1(coordinate, [Crow(1), Crow, Csurf], targetRadius, 'linear');
end
