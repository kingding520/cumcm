function result = postprocess_q1(out, cfg, varargin)
if nargin < 2 || isempty(cfg)
    cfg = make_config();
end
if nargin < 1 || isempty(out)
    out = solve_q1(cfg);
end

p = inputParser;
addParameter(p, 'writeCsv', true, @(x) isscalar(x) && (islogical(x) || isnumeric(x)));
addParameter(p, 'outputDir', fullfile(cfg.paths.root, 'outputs', 'q1_tables'), ...
    @(x) ischar(x) || isstring(x));
parse(p, varargin{:});
opt = p.Results;

paperTimes = [100, 300, 600, 900, 1200, 1500, 1800];
radiusCm = [0, 0.5, 1, 1.5, 2];
radiusM = radiusCm * cfg.conversion.cmToM;

temperatureValues = sample_field(out, cfg, paperTimes, radiusM, 'T');
moistureValues = sample_field(out, cfg, paperTimes, radiusM, 'C');
temperatureValues = round(temperatureValues, cfg.output.roundDigits);
moistureValues = round(moistureValues, cfg.output.roundDigits);
variableNames = {'time_s', 'r0_cm', 'r05_cm', 'r10_cm', 'r15_cm', 'r20_cm'};
temperatureTable = array2table([paperTimes(:), temperatureValues], ...
    'VariableNames', variableNames);
moistureTable = array2table([paperTimes(:), moistureValues], ...
    'VariableNames', variableNames);

result = struct('times', paperTimes(:), 'radiusCm', radiusCm, ...
    'temperature', temperatureTable, 'moisture', moistureTable, ...
    'files', struct('temperature', '', 'moisture', ''));

if logical(opt.writeCsv)
    outputDir = char(opt.outputDir);
    if ~isfolder(outputDir)
        mkdir(outputDir);
    end
    result.files.temperature = fullfile(outputDir, 'q1_table1_temperature.csv');
    result.files.moisture = fullfile(outputDir, 'q1_table2_moisture.csv');
    writetable(temperatureTable, result.files.temperature);
    writetable(moistureTable, result.files.moisture);
end
end

function values = sample_field(out, cfg, times, targetRadius, fieldName)
values = zeros(numel(times), numel(targetRadius));
for j = 1:numel(times)
    Trow = interp1(out.t, out.T, times(j), 'linear');
    Crow = interp1(out.t, out.C, times(j), 'linear');
    Tinf = interp1(out.environment.t, out.environment.Tinf, times(j), 'linear', 'extrap');
    Cinf = interp1(out.environment.t, out.environment.Cinf, times(j), 'linear', 'extrap');
    [Tsurf, Csurf] = reconstruct_surface(Trow, Crow, Tinf, Cinf, cfg, ...
        out.radius / size(out.T, 2));
    if strcmp(fieldName, 'T')
        radialValues = [Trow(1), Trow, Tsurf];
    else
        radialValues = [Crow(1), Crow, Csurf];
    end
    radialCoordinate = [0, out.r(:).', out.radius];
    values(j, :) = interp1(radialCoordinate, radialValues, targetRadius, 'linear');
end
end

function [Tsurf, Csurf] = reconstruct_surface(Trow, Crow, Tinf, Cinf, cfg, dr)
lastC = max(Crow(end), 1e-12);
kLast = cfg.properties.q1.k(lastC);
DLast = cfg.properties.q1.D(lastC, Crow(end) * 0 + Trow(end) + cfg.conversion.celsiusToKelvin);
qOut = (Trow(end) - Tinf) / (dr / (2 * kLast) + 1 / cfg.boundary.h);
jOut = (Crow(end) - Cinf) / (dr / (2 * DLast) + 1 / cfg.boundary.hm);
Tsurf = Tinf + qOut / cfg.boundary.h;
Csurf = Cinf + jOut / cfg.boundary.hm;
end
