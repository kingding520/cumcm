function out = solve_q1(cfg, varargin)
if nargin == 0 || isempty(cfg)
    cfg = make_config();
end

p = inputParser;
addParameter(p, 'nCells', cfg.numerics.activeCells, @(x) isscalar(x) && x >= 3 && x == floor(x));
addParameter(p, 'tEval', [], @(x) isempty(x) || (isvector(x) && all(isfinite(x))));
addParameter(p, 'tEnd', [], @(x) isempty(x) || (isscalar(x) && isfinite(x) && x > 0));
addParameter(p, 'environment', [], @(x) isempty(x) || isstruct(x));
parse(p, varargin{:});
opt = p.Results;

if isempty(opt.tEval)
    if isempty(opt.tEnd)
        requestedEnd = 1800;
    else
        requestedEnd = opt.tEnd;
    end
    tEval = unique([0, cfg.output.q1Times(cfg.output.q1Times <= requestedEnd), requestedEnd]);
else
    tEval = unique([0, double(opt.tEval(:).')]);
    if ~isempty(opt.tEnd)
        tEval = unique([tEval, opt.tEnd]);
    end
end
tEval = sort(tEval(tEval >= 0));
environment = resolve_environment(cfg, opt.environment);
assert(environment.t(1) == 0, '环境时间必须从 0 开始。');
assert(environment.t(end) >= max(tEval), ...
    'Q1 求解时间超过环境数据范围；请提供覆盖 tEnd 的 environment。');

nCells = double(opt.nCells);
R = cfg.geometry.radius0;
L = cfg.geometry.length;
dr = R / nCells;
rFaces = (0:nCells)' * dr;
r = 0.5 * (rFaces(1:end-1) + rFaces(2:end));
cellVolume = pi * (rFaces(2:end).^2 - rFaces(1:end-1).^2) * L;
faceArea = 2 * pi * rFaces * L;

y0 = [cfg.initial.T * ones(nCells, 1); cfg.initial.C * ones(nCells, 1)];
absTol = [cfg.numerics.absTolTemperature * ones(nCells, 1); ...
    cfg.numerics.absTolMoisture * ones(nCells, 1)];
odeOptions = odeset('RelTol', cfg.numerics.relTol, ...
    'AbsTol', absTol, 'MaxStep', cfg.numerics.maxInternalStep, ...
    'NonNegative', (nCells + 1):(2 * nCells));

rhs = @(time, state) rhs_q1(time, state, cfg, environment, ...
    cellVolume, faceArea, dr, nCells);
[tSol, ySol] = ode15s(rhs, tEval, y0, odeOptions);

out = struct();
out.t = tSol;
out.r = r;
out.rFaces = rFaces;
out.T = ySol(:, 1:nCells);
out.C = ySol(:, nCells + 1:2 * nCells);
out.environment = environment;
out.radius = R;
out.meta = struct('question', 1, 'method', 'cylindrical FVM + ode15s', ...
    'nCells', nCells, 'timeUnit', 's', 'temperatureUnit', 'degC', ...
    'moistureUnit', 'kg/kg dry basis');
end

function dydt = rhs_q1(time, state, cfg, environment, cellVolume, faceArea, dr, nCells)
T = state(1:nCells);
C = state(nCells + 1:2 * nCells);
Csafe = max(C, 1e-12);
TK = T + cfg.conversion.celsiusToKelvin;
k = cfg.properties.q1.k(Csafe);
D = cfg.properties.q1.D(Csafe, TK);
rho = cfg.properties.q1.rho(Csafe);
cp = cfg.properties.q1.cp(Csafe);
Tinf = interp1(environment.t, environment.Tinf, time, 'linear', 'extrap');
Cinf = interp1(environment.t, environment.Cinf, time, 'linear', 'extrap');

dT = zeros(nCells, 1);
dC = zeros(nCells, 1);
for i = 1:(nCells - 1)
    kFace = 2 * k(i) * k(i + 1) / (k(i) + k(i + 1));
    DFace = 2 * D(i) * D(i + 1) / (D(i) + D(i + 1));
    area = faceArea(i + 1);
    GT = kFace * area / dr;
    GC = DFace * area / dr;
    dT(i) = dT(i) + GT * (T(i + 1) - T(i));
    dT(i + 1) = dT(i + 1) + GT * (T(i) - T(i + 1));
    dC(i) = dC(i) + GC * (C(i + 1) - C(i));
    dC(i + 1) = dC(i + 1) + GC * (C(i) - C(i + 1));
end

surfaceArea = faceArea(end);
GTBoundary = surfaceArea / (dr / (2 * k(end)) + 1 / cfg.boundary.h);
GCBoundary = surfaceArea / (dr / (2 * D(end)) + 1 / cfg.boundary.hm);
dT(end) = dT(end) + GTBoundary * (Tinf - T(end));
dC(end) = dC(end) + GCBoundary * (Cinf - C(end));

dT = dT ./ (rho .* cp .* cellVolume);
dC = dC ./ cellVolume;
dydt = [dT; dC];
end

function environment = resolve_environment(cfg, supplied)
if isempty(supplied)
    data = readtable(cfg.paths.attachment1, 'VariableNamingRule', 'preserve');
    time = data{:, 1};
    Tinf = data{:, 2};
    Cinf = data{:, 3};
else
    required = {'t', 'Tinf', 'Cinf'};
    assert(all(isfield(supplied, required)), 'environment 必须包含 t、Tinf、Cinf。');
    time = supplied.t(:);
    Tinf = supplied.Tinf(:);
    Cinf = supplied.Cinf(:);
end
assert(numel(time) == numel(Tinf) && numel(time) == numel(Cinf), ...
    '环境时间和边界数组长度必须相同。');
assert(all(isfinite(time)) && all(isfinite(Tinf)) && all(isfinite(Cinf)), ...
    '环境数据不得包含 NaN 或 Inf。');
[time, order] = sort(time);
Tinf = Tinf(order);
Cinf = Cinf(order);
assert(time(1) == 0 && all(diff(time) > 0), '环境时间必须严格递增。');
environment = struct('t', time, 'Tinf', Tinf, 'Cinf', Cinf);
end
