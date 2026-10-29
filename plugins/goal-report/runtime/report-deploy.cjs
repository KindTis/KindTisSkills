// Reuses pinned Wrangler and encrypted keyring OAuth. No credential output is retained.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const net=require('node:net');
const {spawnSync}=require('node:child_process');
const LIMIT=24*1024*1024;

class SafeError extends Error {}

function requireImplementationCompletion(request) {
 if(request?.report_type!=='implementation_completion'||!['implementation','game'].includes(request?.kind)) {
  throw new SafeError('Cloudflare 업로드는 구현 완료 보고서만 허용합니다. 명세서·계획서·검증 보고서는 업로드 대상이 아닙니다.');
 }
}

function requireGoalReadiness(request) {
 requireImplementationCompletion(request);
 const requested=typeof request?.publication_request==='string'&&/^[0-9a-f]{64}$/.test(request?.publication_request_digest||'');
 if((!requested&&(typeof request?.goal_id!=='string'||!request.goal_id||typeof request?.thread_id!=='string'||!request.thread_id))||typeof request?.root!=='string'||typeof request?.event_key!=='string') {
  throw new SafeError('Cloudflare 업로드는 실제 goal의 완료 준비가 확인된 구현 완료 보고서만 허용합니다.');
 }
 const check=spawnSync(request.python||'python',[path.join(__dirname,'report-policy.py')],{
  input:JSON.stringify(request),encoding:'utf8',windowsHide:true,timeout:10000,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1'}
 });
 let result;try {result=JSON.parse(check.stdout||'{}');}catch {}
 if(check.status!==0||result?.eligible!==true) throw new SafeError('구현 완료 보고서의 실제 goal 연결과 완료 준비 fingerprint를 확인하지 못했습니다.');
}
function isProjectIndex(file) {return /^projects\/[a-z0-9][a-z0-9_-]*\/index\.html$/.test(file);}
function validateNavigationMigration(file,current,config) {
 const changes=config.navigation_migrations||[];
 const entry=changes.find(item=>item.path===file.path);
 if(!entry||entry.before_sha256!==file.sha256||entry.after_sha256!==current?.sha256||!/^projects\/[a-z0-9][a-z0-9_-]*\/[a-z0-9][a-z0-9_-]*\/index\.html$/.test(file.path)) return false;
 const beforePath=path.join(config.root,'published-objects',file.sha256);
 if(!fs.existsSync(beforePath)||fs.lstatSync(beforePath).isSymbolicLink()) return false;
 const before=fs.readFileSync(beforePath);
 if(crypto.createHash('sha256').update(before).digest('hex')!==file.sha256) return false;
 if(typeof entry.href!=='string'||!/^\.\.\/(?:\.\.\/[a-z0-9][a-z0-9_-]*\/)?index\.html$/.test(entry.href)) return false;
 const target=path.posix.normalize(path.posix.join(path.posix.dirname(file.path),entry.href));
 if(!isProjectIndex(target)) return false;
 const text=before.toString('utf8');
 const pattern=/(<a (?:class="back" )?href=")[^"<>]*(">← 보고서 모음<\/a>)/g;
 if([...text.matchAll(pattern)].length!==1) return false;
 const after=Buffer.from(text.replace(pattern,(_,start,end)=>start+entry.href+end),'utf8');
 return crypto.createHash('sha256').update(after).digest('hex')===current.sha256&&after.length===current.bytes;
}

function contextFor(request={}) {
 const seconds=request.deployment_timeout_seconds??300;
 if(!Number.isFinite(seconds)||seconds<1||seconds>900) throw new SafeError('배포 시간 예산은 1~900초여야 합니다.');
 const context={deadline:performance.now()+seconds*1000,state:{deployment_status:'not_applied',deployed:false,phase:'start',candidate_digest:request.candidate_digest||null}};
 context.progress=(phase,extra={})=>{
  Object.assign(context.state,{phase,...extra});
  if(request.root&&request.event_key&&/^[0-9a-f]{24}$/.test(request.event_key)) {
   const file=path.join(request.root,'deployment-attempts',request.event_key+'.json');
   fs.mkdirSync(path.dirname(file),{recursive:true});
   const temp=file+'.'+process.pid+'.tmp';
   fs.writeFileSync(temp,JSON.stringify({...context.state,updated_at:new Date().toISOString()}));fs.renameSync(temp,file);
  }
  if(request.log_progress) process.stderr.write(JSON.stringify({phase,...extra})+'\n');
 };
 return context;
}
function remaining(context,maximum) {
 const time=Math.floor(context.deadline-performance.now());
 if(time<=0) throw new SafeError('전체 배포 시간 예산 초과; 단계 '+context.state.phase);
 return Math.min(time,maximum);
}
function errorCode(error) {
 const code=error?.cause?.code||error?.code||error?.name||'NETWORK_ERROR';
 return /^[A-Za-z0-9_]{1,60}$/.test(code)?code:'NETWORK_ERROR';
}
async function readRequest(url,options={},context=contextFor(),maximum=20000) {
 const label=new URL(url).pathname;
 for(let attempt=0;attempt<3;attempt++) {
  let response,code,delay;
  try {
   response=await fetch(url,{...options,signal:AbortSignal.timeout(remaining(context,maximum))});
   if(![429,500,502,503,504].includes(response.status)) return response;
   code='HTTP_'+response.status;
   const raw=response.headers.get('retry-after');
   delay=raw&&/^\d+(?:\.\d+)?$/.test(raw)?Number(raw)*1000:raw?Math.max(0,Date.parse(raw)-Date.now()):null;
   await response.body?.cancel();
  } catch(error) {
   if(error instanceof SafeError) throw error;
   code=errorCode(error);
  }
  if(attempt===2) throw new SafeError(`읽기 요청 실패: ${label} / ${code} / 시도 3회`);
  delay=Number.isFinite(delay)?delay:200*(2**attempt);
  if(delay>=remaining(context,900000)) throw new SafeError(`재시도 대기 시간 예산 초과: ${label} / ${code}`);
  context.progress(context.state.phase,{retry_path:label,retry_code:code,retry_attempt:attempt+1});
  await new Promise(resolve=>setTimeout(resolve,delay));
 }
}

function safeEnv() {
 const env={...process.env,CLOUDFLARE_AUTH_USE_KEYRING:'true',WRANGLER_WRITE_LOGS:'false',WRANGLER_SEND_METRICS:'false',WRANGLER_SEND_ERROR_REPORTS:'false',CI:'true'};
 for(const key of ['CLOUDFLARE_API_TOKEN','CF_API_TOKEN','CLOUDFLARE_API_KEY','CF_API_KEY','CLOUDFLARE_EMAIL','CF_EMAIL']) delete env[key];
 return env;
}
function host(value,label) {
 if(typeof value!=='string'||!value||value.trim()!==value||/[\s/@?#:]/.test(value)) throw new SafeError(`${label} 호스트 설정이 잘못되었습니다.`);
 const normalized=value.toLowerCase();
 if(net.isIP(normalized)||normalized.length>253||!normalized.includes('.')||normalized.split('.').some(part=>!part||part.length>63||!/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(part))) throw new SafeError(`${label} 호스트 설정이 잘못되었습니다.`);
 return normalized;
}
function configFor(input) {
 if(!input||typeof input!=='object'||Array.isArray(input)) throw new SafeError('배포 설정이 없습니다.');
 const required=['account','worker_id','worker_name','base_url','wrangler','wrangler_version','access_host','compatibility_date'];
 if(required.some(key=>typeof input[key]!=='string'||!input[key].trim())) throw new SafeError('필수 배포 설정이 없거나 잘못되었습니다.');
 if(!/^[a-f0-9]{32}$/i.test(input.account)||!/^[a-f0-9]{32}$/i.test(input.worker_id)) throw new SafeError('Cloudflare account 또는 Worker ID 설정이 잘못되었습니다.');
 if(!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(input.worker_name)) throw new SafeError('Worker 이름 설정이 잘못되었습니다.');
 if(!path.isAbsolute(input.wrangler)) throw new SafeError('Wrangler 경로 설정이 절대 경로가 아닙니다.');
 if(!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(input.wrangler_version)) throw new SafeError('Wrangler 버전 설정이 잘못되었습니다.');
 let compatibilityDate;
 try {compatibilityDate=new Date(`${input.compatibility_date}T00:00:00.000Z`).toISOString().slice(0,10);} catch {throw new SafeError('Workers compatibility date 설정이 잘못되었습니다.');}
 if(!/^\d{4}-\d{2}-\d{2}$/.test(input.compatibility_date)||compatibilityDate!==input.compatibility_date) throw new SafeError('Workers compatibility date 설정이 잘못되었습니다.');
 let base;
 try {base=new URL(input.base_url);} catch {throw new SafeError('base_url은 올바른 HTTPS 호스트여야 합니다.');}
 if(base.protocol!=='https:'||!base.hostname||base.username||base.password||base.port||!['','/'].includes(base.pathname)||base.search||base.hash) throw new SafeError('base_url은 올바른 HTTPS 호스트여야 합니다.');
 const baseHost=host(base.hostname,'base_url');
 const accessHost=host(input.access_host,'Access');
 return {...input,account:input.account.toLowerCase(),worker_id:input.worker_id.toLowerCase(),base_url:`https://${baseHost}`,access_host:accessHost};
}
function authenticate(config,context=contextFor()) {
 const settings=configFor(config);
 let installed;
 try {installed=JSON.parse(fs.readFileSync(path.join(settings.wrangler,'package.json'),'utf8'));}
 catch {throw new SafeError('설정된 Wrangler 패키지를 읽을 수 없습니다.');}
 if(installed.version!==settings.wrangler_version) throw new SafeError('기존 Wrangler 버전이 바뀌었습니다. 배포를 보류합니다.');
 let child;
 try {
  child=spawnSync(process.execPath,[path.join(settings.wrangler,'bin','wrangler.js'),'auth','token','--json'],{env:safeEnv(),encoding:'utf8',windowsHide:true,timeout:remaining(context,60000),stdio:['ignore','pipe','pipe']});
 } catch {throw new SafeError('기존 암호화 keyring OAuth를 사용할 수 없습니다. 권한 추가 없이 중단했습니다.');}
 let stdout=child?.stdout||'';
 if(child?.status!==0||child.error) {stdout='';if(child){child.stdout='';child.stderr='';}throw new SafeError('기존 암호화 keyring OAuth를 사용할 수 없습니다. 권한 추가 없이 중단했습니다.');}
 let auth;
 try {auth=JSON.parse(stdout);} catch {throw new SafeError('기존 OAuth 확인 실패');} finally {stdout='';if(child){child.stdout='';child.stderr='';}}
 if(auth.type!=='oauth'||typeof auth.token!=='string'||!auth.token) throw new SafeError('기존 OAuth가 아닙니다.');
 return auth;
}
async function api(token,endpoint,options={},context=contextFor()) {
 let res,body;
 try {
  res=await readRequest('https://api.cloudflare.com/client/v4'+endpoint,{...options,headers:{Authorization:'Bearer '+token,...options.headers},redirect:'error'},context,45000);
  body=await res.json();
 } catch(error) {if(error instanceof SafeError) throw error;throw new SafeError('Cloudflare 응답 JSON 확인 실패: '+endpoint.split('?')[0]);}
 if(!res.ok||!body.success) throw new SafeError('Cloudflare 요청 실패; 상태 '+res.status+' / 코드 '+(body.errors||[]).map(error=>String(error.code||'')).join(','));
 return body.result;
}
function accessHost(value) {
 try {return host(value,'Access');} catch {throw new SafeError('Access host 설정이 없거나 잘못되었습니다.');}
}
async function probe(url,headers={},expectedAccessHost,context=contextFor()) {
 const expected=accessHost(expectedAccessHost);
 let parsed;
 try {parsed=new URL(url);} catch {throw new SafeError('Access 검사 URL이 잘못되었습니다.');}
 if(parsed.protocol!=='https:'||parsed.username||parsed.password) throw new SafeError('Access 검사 URL은 HTTPS여야 합니다.');
 let res;
 res=await readRequest(url,{headers,redirect:'manual'},context);
 const raw=res.headers.get('location');
 let dest=null;
 try {dest=raw?new URL(raw,url):null;} catch {}
 await res.body?.cancel();
 if(![302,303,307,308].includes(res.status)||!dest||dest.protocol!=='https:'||dest.username||dest.password||dest.port||dest.hostname.toLowerCase()!==expected||!dest.pathname.startsWith('/cdn-cgi/access/')) throw new SafeError('비로그인 Access 차단 확인 실패: '+parsed.pathname+' / HTTP '+res.status);
 return {url,status:res.status,range:headers.Range||null,access_login_redirect:true};
}
function assetManifest(root) {
 root=fs.realpathSync(root);
 const files=[];
 function walk(dir) {
  for(const entry of fs.readdirSync(dir,{withFileTypes:true})) {
   const full=path.join(dir,entry.name);
   if(entry.isSymbolicLink()) throw new SafeError('심볼릭 링크 자산은 업로드하지 않습니다.');
   const resolved=fs.realpathSync(full);
   if(!resolved.startsWith(root+path.sep)) throw new SafeError('자산 경로가 승인된 public 폴더를 벗어났습니다.');
   if(entry.isDirectory()) {walk(full);continue;}
   if(!entry.isFile()) throw new SafeError('일반 파일만 업로드합니다.');
   const relative=path.relative(root,full).split(path.sep).join('/');
   if(entry.name!=='_headers'&&!/\.(html|css|js|png|jpe?g|webp|mp4|otf)$/i.test(relative)) throw new SafeError('허용되지 않은 자산 파일입니다.');
   const data=fs.readFileSync(full);
   if(data.length>LIMIT) throw new SafeError('24 MiB 자산 제한 초과. 자동 삭제하지 않습니다.');
   files.push({path:relative,bytes:data.length,sha256:crypto.createHash('sha256').update(data).digest('hex')});
  }
 }
 walk(root);
 if(files.length>20000||!files.some(file=>file.path==='index.html')) throw new SafeError('누적 자산 수 또는 루트 보고서 목록이 잘못되었습니다.');
 return files.sort((a,b)=>a.path.localeCompare(b.path));
}
function changedAssets(files,reportPath,config) {
 const prefix=reportPath.slice(1);
 const shared=new Set(['index.html','theme.css','catalogue-theme.css','_headers']);
 const changed=new Set(config.changed_paths||['index.html','theme.css','catalogue-theme.css','_headers']);
 const fonts=new Set(['fonts/ridibatang/v1/RIDIBatang.otf','fonts/ridibatang/v1/RIDIBatang-LICENSE.html']);
 return files.filter(file=>file.path.startsWith(prefix)||fonts.has(file.path)||((shared.has(file.path)||isProjectIndex(file.path)||(config.navigation_migrations||[]).some(item=>item.path===file.path))&&changed.has(file.path)));
}
async function protectedAssets(base,files,reportPath,config,context=contextFor()) {
 const selected=changedAssets(files,reportPath,config);
 const paths=new Set(['/','/__codex_access_probe__',reportPath]);
 for(const file of selected) if(file.path!=='_headers') paths.add('/'+file.path);
 const checks=[];
 for(const assetPath of paths) checks.push(await probe(base+assetPath,{},config.access_host,context));
 for(const video of selected.filter(file=>file.path.endsWith('.mp4'))) checks.push(await probe(base+'/'+video.path,{Range:'bytes=0-1023'},config.access_host,context));
 return checks;
}
async function workerIdentity(token,config,context=contextFor()) {
 const worker=await api(token,`/accounts/${config.account}/workers/workers/${config.worker_id}`,{},context);
 if(worker.id!==config.worker_id||worker.name!==config.worker_name||worker.subdomain?.enabled!==true||worker.subdomain?.previews_enabled!==false) throw new SafeError('Worker 이름·ID·진입점 상태 불일치. 배포를 보류합니다.');
 return worker;
}
async function identity(token,config,context=contextFor()) {
 const worker=await workerIdentity(token,config,context);
 const routes=await api(token,`/accounts/${config.account}/workers/services/${config.worker_name}/environments/production/routes?show_zonename=true`,{},context);
 const domains=await api(token,`/accounts/${config.account}/workers/domains?service=${config.worker_name}&environment=production`,{},context);
 if(routes.length||domains.length) throw new SafeError('검증하지 않은 추가 도메인 또는 route가 있습니다.');
 return worker;
}
async function doctor(config,token) {
 const settings=configFor(config);
 await workerIdentity(token,settings);
 await probe(settings.base_url+'/',{},settings.access_host);
 return {verified:true,authentication:'ready',worker_name:settings.worker_name,base_url:settings.base_url};
}
async function protectionScope(token,config,context) {
 try {
  const apps=await api(token,`/accounts/${config.account}/access/apps?per_page=100&page=1`,{},context);
  const hostname=new URL(config.base_url).hostname;
  const matches=apps.filter(app=>String(app.domain||'').split('/')[0]===hostname||(app.destinations||[]).some(dest=>dest.worker_id===config.worker_id));
  const fingerprint=crypto.createHash('sha256').update(JSON.stringify(matches.sort((a,b)=>String(a.id).localeCompare(String(b.id))))).digest('hex');
  return {status:matches.length&&apps.length<100?'identified':'unresolved',fingerprint};
 } catch(error) {
  if(!(error instanceof SafeError)) throw error;
  return {status:'unresolved',fingerprint:null};
 }
}
function viewerHeaders() {
 const id=process.env.CODEX_REPORT_ACCESS_CLIENT_ID,secret=process.env.CODEX_REPORT_ACCESS_CLIENT_SECRET,cookie=process.env.CODEX_REPORT_ACCESS_COOKIE;
 if(id&&secret) return {'CF-Access-Client-Id':id,'CF-Access-Client-Secret':secret};
 if(cookie) return {Cookie:'CF_Authorization='+cookie};
 return null;
}
async function verifyContent(base,files,root,headers,context=contextFor()) {
 if(!headers) return {content_status:'not-run',content_verified:false,content_checks:[]};
 const checks=[];
 for(const file of files.filter(f=>f.path!=='_headers')) {
  const ext=path.extname(file.path).toLowerCase();
  const original=new URL(base+'/'+file.path);
  let response=await readRequest(original.href,{headers,redirect:'manual'},context);
  if(ext==='.html'&&[301,302,303,307,308].includes(response.status)) {
   const location=response.headers.get('location'),canonical=new URL(original.href);
   canonical.pathname=original.pathname.endsWith('/index.html')?original.pathname.slice(0,-10):original.pathname.slice(0,-5);
   canonical.search='';canonical.hash='';
   let destination;
   try {destination=location?new URL(location,original):null;}catch {}
   await response.body?.cancel();
   if(!destination||destination.href!==canonical.href) throw new SafeError('인증 후 HTML 정규화 경로 확인 실패: /'+file.path);
   response=await readRequest(destination.href,{headers,redirect:'manual'},context);
  }
  const mime=(response.headers.get('content-type')||'').split(';')[0].toLowerCase();
  const expected={'.html':['text/html'],'.css':['text/css'],'.js':['application/javascript','text/javascript'],'.png':['image/png'],'.jpg':['image/jpeg'],'.jpeg':['image/jpeg'],'.webp':['image/webp'],'.mp4':['video/mp4'],'.otf':['font/otf','font/opentype']}[ext]||[];
  if(response.status!==200||!expected.includes(mime)) {await response.body?.cancel();throw new SafeError('인증 후 콘텐츠 상태·MIME 확인 실패: /'+file.path+' / HTTP '+response.status);}
  const data=await boundedBody(response,file.bytes,file.path);
  const bytes=data.length;
  if(bytes!==file.bytes||crypto.createHash('sha256').update(data).digest('hex')!==file.sha256) throw new SafeError('인증 후 콘텐츠 해시 불일치: /'+file.path);
  checks.push({path:file.path,status:200,mime,sha256:file.sha256});
  if(ext==='.mp4'&&bytes) {
   const end=Math.min(1023,bytes-1);
   const range=await readRequest(base+'/'+file.path,{headers:{...headers,Range:`bytes=0-${end}`},redirect:'manual'},context);
   if(range.status!==206||range.headers.get('content-range')!==`bytes 0-${end}/${bytes}`) {await range.body?.cancel();throw new SafeError('인증 후 영상 부분 응답 확인 실패: /'+file.path);}
   const partial=await boundedBody(range,end+1,file.path);
   if(!partial.equals(data.subarray(0,end+1))) throw new SafeError('인증 후 영상 부분 내용 불일치: /'+file.path);
   checks.push({path:file.path,status:206,range:`bytes=0-${end}`});
  }
 }
 return {content_status:'passed',content_verified:true,content_checks:checks};
}
async function boundedBody(response,maximum,label) {
 const reader=response.body?.getReader();
 if(!reader) return Buffer.alloc(0);
 const chunks=[];let bytes=0;
 try {
  while(true) {
   const {value,done}=await reader.read();if(done) break;
   bytes+=value.length;
   if(bytes>maximum) {await reader.cancel();throw new SafeError('인증 후 콘텐츠 크기 초과: /'+label);}
   chunks.push(Buffer.from(value));
  }
  return Buffer.concat(chunks);
 } catch(error) {
  if(error instanceof SafeError) throw error;
  throw new SafeError('인증 후 콘텐츠 수신 실패: /'+label+' / '+errorCode(error));
 } finally {reader.releaseLock();}
}
async function deploy(request,token,context=contextFor(request)) {
 requireGoalReadiness(request);
 const config=configFor(request);
 if(typeof request.root!=='string'||!request.root||typeof request.public!=='string'||!request.public) throw new SafeError('배포 root 또는 public 경로 설정이 없습니다.');
 if(typeof request.report_path!=='string'||typeof request.event_key!=='string'||!/^\/projects\/[a-z0-9][a-z0-9_-]*\/[0-9a-f]{24}\/$/.test(request.report_path)||!/^[0-9a-f]{24}$/.test(request.event_key)) throw new SafeError('보고서 경로 또는 event key가 잘못되었습니다.');
 const files=assetManifest(request.public);
 if(!files.some(file=>'/'+file.path===request.report_path+'index.html')) throw new SafeError('선택한 보고서가 누적 자산에 없습니다.');
 const previous=request.previous_assets||[];
 if(!Array.isArray(previous)) throw new SafeError('기존 게시 자산 목록이 잘못되었습니다.');
 const current=new Map(files.map(file=>[file.path,file]));
 for(const file of previous) {
  if(file.path.startsWith(request.report_path.slice(1))||['index.html','theme.css','catalogue-theme.css','_headers'].includes(file.path)||isProjectIndex(file.path)) continue;
  if(validateNavigationMigration(file,current.get(file.path),request)) continue;
  if(current.get(file.path)?.sha256!==file.sha256||current.get(file.path)?.bytes!==file.bytes) throw new SafeError('기존 게시 자산을 누락·변경한 후보입니다: /'+file.path);
 }
 context.progress('identity');
 await identity(token,config,context);
 const digest=crypto.createHash('sha256').update(JSON.stringify(files)).digest('hex');
 const annotation=config.worker_name+' '+request.event_key+' '+digest;
 const history=await api(token,`/accounts/${config.account}/workers/scripts/${config.worker_name}/deployments`,{},context);
 let active=history.deployments[0];
 const activeDigest=String(active?.annotations?.['workers/message']||'').split(' ').at(-1);
 const reused=activeDigest===(request.action==='status'?(request.expected_asset_digest||digest):digest);
 if(request.action==='status'&&(!reused||digest!==(request.expected_asset_digest||digest))) return {verified:false,deployed:null,deployment_status:'stale',active_asset_digest:activeDigest,version_id:active?.versions?.[0]?.version_id||null};
 if(!reused&&request.baseline_digest&&activeDigest!==request.baseline_digest) throw new SafeError('현재 원격 버전이 게시 기준과 다릅니다. 불확실한 이전 배포를 먼저 확인하세요.');
 if(!reused&&!request.baseline_digest&&active) throw new SafeError('기존 원격 보고서의 게시 기준이 없습니다. 자동 덮어쓰기를 보류합니다.');
 let before=[];
 context.progress('protection-scope');
 const scopeBefore=await protectionScope(token,config,context);
 if(!reused) {
  context.progress('before-protection');
  before=await protectedAssets(config.base_url,files,request.report_path,config,context);
  const cfg=path.join(request.root,'wrangler.jsonc');
  fs.writeFileSync(cfg,JSON.stringify({name:config.worker_name,account_id:config.account,compatibility_date:config.compatibility_date,workers_dev:true,preview_urls:false,assets:{directory:path.resolve(request.public),not_found_handling:'none'}},null,2));
  let child;
  context.progress('apply',{deployment_status:'unknown',deployed:null,asset_digest:digest});
  try {child=spawnSync(process.execPath,[path.join(config.wrangler,'bin','wrangler.js'),'deploy','--config',cfg,'--no-autoconfig','--message',annotation],{env:safeEnv(),cwd:request.root,encoding:'utf8',windowsHide:true,timeout:remaining(context,180000),stdio:['ignore','pipe','pipe']});}
  catch {throw new SafeError('Wrangler 배포 실행에 실패했습니다. 원시 출력은 기록하지 않았습니다.');}
  if(child) {context.progress('resolve-apply',{process_status:child.status,process_error:child.error?errorCode(child.error):null});child.stdout='';child.stderr='';}
  const historyAfter=await api(token,`/accounts/${config.account}/workers/scripts/${config.worker_name}/deployments`,{},context);
  active=historyAfter.deployments[0];
  if(active?.annotations?.['workers/message']!==annotation) throw new SafeError('배포 버전 적용을 확인하지 못했습니다. 중복 업로드 없이 상태를 재조회하세요.');
 }
 context.progress('resolve-version',{deployment_status:'applied_unverified',deployed:true,asset_digest:digest});
 if(active.versions?.length!==1||active.versions[0].percentage!==100) throw new SafeError('보고서 배포 버전이 100% 적용되지 않았습니다.');
 context.progress('after-protection',{deployment_status:'applied_unverified',deployed:true,version_id:active.versions[0].version_id,deployment_id:active.id,asset_digest:digest});
 await identity(token,config,context);
 const after=await protectedAssets(config.base_url,files,request.report_path,config,context);
 const scopeAfter=await protectionScope(token,config,context);
 if(scopeBefore.fingerprint!==scopeAfter.fingerprint) throw new SafeError('배포 중 Access 설정 조회 결과가 변경됐습니다. 현재 상태를 다시 확인하세요.');
 context.progress('content');
 const content=await verifyContent(config.base_url,changedAssets(files,request.report_path,config),request.public,viewerHeaders(),context);
 if(crypto.createHash('sha256').update(JSON.stringify(assetManifest(request.public))).digest('hex')!==digest) throw new SafeError('배포 중 게시 후보 바이트가 변경되었습니다.');
 context.progress('verified',{deployment_status:'verified',deployed:true});
 return {verified:true,deployed:true,deployment_status:'verified',url:config.base_url+request.report_path,worker_id:config.worker_id,version_id:active.versions[0].version_id,deployment_id:active.id,asset_digest:digest,assets:files,asset_count:files.length,reused_deployment:reused,checked_at:new Date().toISOString(),before,after,...content,protection_scope:scopeAfter.status,policy_fingerprint:scopeAfter.fingerprint,credentials_logged:false,access_policy_changed:false};
}
function safeFailure(error) {
 return error instanceof SafeError?error.message:'배포 도구 실행 실패. 자격증명과 원시 출력은 기록하지 않았습니다.';
}

module.exports={assetManifest,safeEnv,authenticate,probe,identity,workerIdentity,protectedAssets,doctor,deploy,verifyContent,contextFor,changedAssets};
if(require.main===module) {
 let raw='';process.stdin.setEncoding('utf8');process.stdin.on('data',chunk=>raw+=chunk);
 process.stdin.on('end',async()=>{
  let auth,context;
  try {
   let request;
   try {request=JSON.parse(raw);} catch {throw new SafeError('요청 JSON을 읽을 수 없습니다.');}
   if(request.action==='manifest') {const assets=assetManifest(request.public);console.log(JSON.stringify({assets,asset_digest:crypto.createHash('sha256').update(JSON.stringify(assets)).digest('hex')}));return;}
   if(request.action!==undefined&&!['deploy','doctor','status'].includes(request.action)) throw new SafeError('지원하지 않는 action입니다.');
   if(request.action!=='doctor') requireGoalReadiness(request);
   const config=configFor(request);
   context=contextFor(request);context.progress('authenticate');
   auth=authenticate(config,context);
   const result=request.action==='doctor'?await doctor(config,auth.token):await deploy(request,auth.token,context);
   auth.token='';
   console.log(JSON.stringify(result));
  } catch(error) {
   if(auth) auth.token='';
   console.log(JSON.stringify({verified:false,...context?.state,error:safeFailure(error)}));
   process.exitCode=1;
  }
 });
}
