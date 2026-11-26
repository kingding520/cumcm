"""Read-only B-data audit. Writes diagnostics, never cleans or overwrites inputs."""
from pathlib import Path
import sys, json, hashlib, warnings, re
from datetime import datetime
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tmp/b_audit_deps'))
import pandas as pd
warnings.filterwarnings('ignore', message='Workbook contains no default style')
SRC = ROOT / 'B题：炼钢厂钢包周转与排产优化问题/附数据文件'
OUT = ROOT / 'data/audit/b_initial_20261126'
OUT.mkdir(parents=True, exist_ok=True)
frames, manifests, profiles, issues, checks = {}, [], [], [], []

def norm(s):
    return s.astype('string').str.strip().str.replace(r'\.0$', '', regex=True).replace('', pd.NA)

def timecol(s):
    # Explicit dates only: numeric durations must not be interpreted as timestamps.
    return pd.to_datetime(s.astype('string').where(s.astype('string').str.match(r'^\d{4}[-/]')), errors='coerce', format='mixed')

def issue(df, mask, file, rule, cols):
    for idx in df.index[mask.fillna(False)]:
        issues.append(dict(file=file, excel_row=int(idx)+2, rule=rule,
                           heat=str(df.at[idx,'熔炼号']) if '熔炼号' in df else '',
                           detail=json.dumps({c:str(df.at[idx,c]) for c in cols},ensure_ascii=False)))

for p in sorted(SRC.glob('*.xls*')):
    input_hash=hashlib.sha256(p.read_bytes()).hexdigest()
    xl = pd.ExcelFile(p)
    for sheet in xl.sheet_names:
        df = pd.read_excel(xl, sheet_name=sheet)
        name = p.name if len(xl.sheet_names)==1 else p.name+'::'+sheet
        frames[name] = df
        manifest=dict(file=p.name,sheet=sheet,rows=len(df),columns=len(df.columns),
                      sha256=input_hash, duplicate_rows=int(df.duplicated().sum()))
        manifests.append(manifest)
        for c in df:
            s=df[c]; nums=pd.to_numeric(s,errors='coerce'); ts=timecol(s)
            profiles.append(dict(file=name,column=c,rows=len(df),missing=int(s.isna().sum()),
                missing_pct=round(100*s.isna().mean(),3),unique=int(s.nunique()),
                zeros=int((nums==0).sum()),minus2=int((norm(s)=='-2').sum()),
                numeric_count=int(nums.notna().sum()),date_count=int(ts.notna().sum()),
                min_date=str(ts.min()) if ts.notna().any() else '',max_date=str(ts.max()) if ts.notna().any() else '',
                top_values=json.dumps({str(k):int(v) for k,v in s.value_counts().head(6).items()},ensure_ascii=False)))
        if '熔炼号' in df:
            keys=norm(df['熔炼号']); valid=keys.notna() & ~keys.isin(['-2','0','-1'])
            manifest.update(missing_heat=int(keys.isna().sum()), placeholder_heat=int(keys.isin(['-2','0','-1']).sum()),
                            valid_heat_rows=int(valid.sum()), unique_valid_heats=int(keys[valid].nunique()),
                            repeated_valid_heat_rows=int(keys[valid].duplicated(keep=False).sum()))
            issue(df,~valid,name,'invalid_heat',['熔炼号'])
            issue(df,valid & keys.duplicated(keep=False),name,'repeated_heat',['熔炼号'])
        pairs=[]
        if name in ['A炉.xlsx','B炉.xlsx','C炉.xlsx']:
            pairs=[('吹炼开始时间','吹炼结束时间'),('出钢开始时间','出钢结束时间'),('吹炼结束时间','出钢开始时间')]
        elif 'LF.xlsx' in name:
            pairs=[('进处理位时间','出处理位时间'),('精炼处理开始时间','精炼处理结束时间'),
                   ('重包进站到LF精炼待机位时间','LF重包从待机位吊起时间')]
        elif 'RH.xlsx' in name:
            pairs=[('脱气开始时间','脱气结束时间'),('进处理位时间','出处理位时间'),('重包到RH接受位时间','重包从RH吊起时间')]
        elif '连铸.xlsx' in name:
            pairs=[('大包开浇时刻','大包终浇时刻'),('重包到铸机接受位座包时间','大包开浇时刻'),
                   ('大包终浇时刻','空包在铸机接受位吊起时间'),('翻渣开始时间1','翻渣结束时间1')]
        elif name=='工序时间信息.xlsx':
            pairs=[('出钢开始时间','出钢结束时间'),('出钢结束时间','重罐吊起时间'),
                   ('重罐吊起时间','重罐落到LF待机位时间'),('LF进处理位时间','LF出处理位时间'),
                   ('LF出处理位时间','LF重罐吊起时间'),('RH处理开始时间','RH处理结束时间'),
                   ('RH处理结束时间','RH吊起时间'),('吊至连铸机时间','开浇时间'),
                   ('开浇时间','停浇时间'),('停浇时间','空包在连铸位吊起时间'),
                   ('翻渣开始时间','翻渣结束时间'),('空罐放置热修位时间','从热修位吊起时间')]
        for a,b in pairs:
            ta,tb=timecol(df[a]),timecol(df[b]); delta=(tb-ta).dt.total_seconds()
            checks.append(dict(file=name,start=a,end=b,paired=int(delta.notna().sum()),negative=int((delta<0).sum()),
                               zero=int((delta==0).sum()),median_s=delta.median() if delta.notna().any() else None,
                               p95_s=delta.quantile(.95) if delta.notna().any() else None))
            issue(df,delta<0,name,'time_reversed:'+a+'→'+b,[a,b])
            issue(df,delta==0,name,'zero_interval:'+a+'→'+b,[a,b])

    xl.close()
    assert hashlib.sha256(p.read_bytes()).hexdigest()==input_hash, f'Input changed during audit: {p.name}'

master=frames['工序时间信息.xlsx'].copy()
master['key']=norm(master['熔炼号'])
cross=[]; offsets=[]
for name,df in frames.items():
    if name=='工序时间信息.xlsx': continue
    keys=norm(df['熔炼号']); valid=keys.notna() & ~keys.isin(['-2','0','-1'])
    masterkeys=set(master['key'].dropna())
    cross.append(dict(file=name,valid_rows=int(valid.sum()),unique_heats=int(keys[valid].nunique()),
                      rows_in_master=int((valid & keys.isin(masterkeys)).sum()),
                      rows_not_in_master=int((valid & ~keys.isin(masterkeys)).sum())))
    issue(df,valid & ~keys.isin(masterkeys),name,'heat_not_in_master',['熔炼号'])
    if name in ['A炉.xlsx','B炉.xlsx','C炉.xlsx']:
        # Offset is in raw physical row order; do not mutate either key or event columns.
        for shift in [-2,-1,0,1,2]:
            temp=pd.DataFrame({'key':keys,'t':timecol(df['出钢开始时间']).shift(shift)})
            both=temp.merge(master[['key','出钢开始时间']],on='key')
            delta=(both['t']-timecol(both['出钢开始时间'])).dt.total_seconds()
            offsets.append(dict(file=name,time_column_shift=shift,paired=int(delta.notna().sum()),
                                within_1s=int((delta.abs()<=1).sum()),median_abs_s=delta.abs().median()))

plan=frames['出钢计划表单.xls']; pk=norm(plan['熔炼号']); pv=pk.notna() & ~pk.isin(['-2','0','-1'])
summary=dict(scope='12 source workbooks, all sheets, all loaded rows; read-only diagnostics',
             python=sys.version, pandas=pd.__version__,
             plan_routes_all=plan['精炼路径'].value_counts(dropna=False).to_dict(),
             plan_routes_valid=plan.loc[pv,'精炼路径'].value_counts(dropna=False).to_dict(),
             plan_valid_unique_casts=int(plan.loc[pv,'连连浇号'].nunique()),
             plan_valid_changed_tundish_counts={str(k):int(v) for k,v in plan.loc[pv,'更换中包'].value_counts(dropna=False).items()},
             issue_count=len(issues))
for filename,rows in [('file_manifest.csv',manifests),('column_profile.csv',profiles),('issues.csv',issues),
                      ('time_checks.csv',checks),('cross_table_coverage.csv',cross),('converter_offset_checks.csv',offsets)]:
    pd.DataFrame(rows).to_csv(OUT/filename,index=False,encoding='utf-8-sig')
weights={}
lf=pd.concat([frames['1#LF.xlsx'],frames['3#LF.xlsx']])
rh=pd.concat([frames['1#RH.xlsx'],frames['2#RH.xlsx']])
cc=pd.concat([frames[f'{i}#连铸.xlsx'] for i in [1,2,3]])
for label,d,col in [('LF',lf,'进入重量'),('RH',rh,'RH起吊重包重量'),
                    ('CC重包',cc,'铸机接受位重包放下重量'),('CC空包',cc,'铸机接受位空包吊起重量')]:
    n=pd.to_numeric(d[col],errors='coerce')
    weights[label]=dict(n=int(n.notna().sum()),gt150=int((n>150).sum()),gt450=int((n>450).sum()),
                        min=float(n.min()),max=float(n.max()))
(OUT/'weight_checks.json').write_text(json.dumps(weights,ensure_ascii=False,indent=2),encoding='utf-8')
cv=pd.concat([frames[f'{i}炉.xlsx'].assign(source=f'{i}炉.xlsx',excel_row=lambda d:d.index+2)
              for i in 'ABC'],ignore_index=True)
identity=[]
for label,d in [('LF',lf),('RH',rh),('CC',cc)]:
    a=cv[['source','excel_row','熔炼号','钢水罐临时罐号']].dropna(subset=['钢水罐临时罐号'])
    b=d[['熔炼号','临时罐号']].dropna(subset=['临时罐号'])
    j=a.merge(b,left_on='钢水罐临时罐号',right_on='临时罐号',suffixes=('_converter','_stage'))
    bad=j[j['熔炼号_converter']!=j['熔炼号_stage']]
    identity.append(dict(stage=label,joined_pairs=len(j),heat_disagreement_pairs=len(bad),
                         by_converter=bad.source.value_counts().to_dict()))
    j.to_csv(OUT/f'temp_identity_{label}.csv',index=False,encoding='utf-8-sig')
summary['temporary_key_checks']=identity
summary['all_null_file_columns']=sum(r['missing']==r['rows'] for r in profiles)
summary['profiled_file_columns']=len(profiles)
slotcols=[f'精炼{i}时间' for i in range(1,9)]
plan.groupby('精炼路径')[slotcols].count().to_csv(OUT/'plan_route_slot_presence.csv',encoding='utf-8-sig')
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(pd.DataFrame(manifests).drop(columns=['sha256']).to_string(index=False))
print(pd.DataFrame(checks).to_string(index=False))
print('CROSS TABLE');print(pd.DataFrame(cross).to_string(index=False))
print('OFFSET');print(pd.DataFrame(offsets).to_string(index=False))
print(json.dumps(summary,ensure_ascii=False,default=str))
