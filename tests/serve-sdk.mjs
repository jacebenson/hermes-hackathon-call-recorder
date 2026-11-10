// Isolated loopback proof using the installed production loader, SDK and CSS.
// Does not install anything in a real Hermes home or attach to a live session.
import { createRequire } from 'node:module'
import { readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'node:http'
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const desktop = process.env.HERMES_DESKTOP || '/home/jace/.hermes/hermes-agent/.build/webapp-workspace/apps/desktop'
const src = join(desktop, 'src'), workspace = resolve(desktop, '../..')
const { build } = createRequire(join(desktop, 'package.json'))('esbuild')
const out = process.env.PROOF_OUT || join(process.env.TMPDIR, 'call-recorder-plugin-proof')
mkdirSync(out, { recursive: true })
const cssDir = process.env.HERMES_CSS_DIR || '/home/jace/.hermes/hermes-agent/apps/desktop/dist-webapp/assets'
const cssName = readdirSync(cssDir).find(n => /^main-.*\.css$/.test(n))
const entry = join(out, 'entry.jsx')
writeFileSync(entry, `
import React,{useState} from 'react';import{createRoot}from'react-dom/client';
import{ThemeProvider,useTheme}from'${src}/themes/context';
import{loadRuntimePlugin}from'${src}/contrib/runtime-loader';
import{registry}from'${src}/contrib/registry';
import{useContributions}from'${src}/contrib/react/use-contributions';
import{$pluginRecords,$pluginDecisions,setPluginEnabled}from'${src}/contrib/plugins-store';
import{host}from'${src}/sdk/index';
import{$connection}from'${src}/store/session';
import{$activeGatewayProfile}from'${src}/store/profile';
$connection.set({mode:'remote',connectionId:'fixture-remote-a'});$activeGatewayProfile.set('fixture-profile-a');
window.proof={errors:[]};window.addEventListener('error',e=>proof.errors.push(e.message));
window.disposePlugin=()=>setPluginEnabled('call-recorder',false);
window.backendSwitch=()=>{ $connection.set({mode:'remote',connectionId:'fixture-remote-b'});$activeGatewayProfile.set('fixture-profile-b');return {connection:host.state.connectionId.get(),profile:host.state.profile.get()} };
function Slot(){return useContributions('panes').map(c=><React.Fragment key={c.id}>{c.render?.()}</React.Fragment>)}
function App(){let theme=useTheme();let[mount,setMount]=useState(true);window.setTheme=theme.setMode;window.mountPane=setMount;return <div style={{height:'100vh',background:'var(--ui-bg-primary)',color:'var(--ui-text-primary)'}}><SlotWrap mount={mount}/></div>}
function SlotWrap({mount}){return mount?<Slot/>:null}
$pluginDecisions.set({'call-recorder':true});
await loadRuntimePlugin(await(await fetch('/plugin.js')).text(),'fixture/client-local');
window.proof.records=$pluginRecords.get();window.proof.contributions=registry.getArea('panes').map(c=>({id:c.id,source:c.source,title:c.title}));
createRoot(document.getElementById('root')).render(<ThemeProvider><App/></ThemeProvider>);
`)
await build({plugins:[{name:'fixture-no-bundled-discovery',setup(b){b.onLoad({filter:/\/catalog-curation\.ts$/},args=>({contents:readFileSync(args.path,'utf8').replace(/import\.meta\.glob\([\s\S]*?\)/g,'({})'),loader:'ts'}))}}],entryPoints:[entry],outfile:join(out,'bundle.js'),bundle:true,format:'esm',jsx:'automatic',nodePaths:[join(desktop,'node_modules'),join(workspace,'node_modules')],alias:{'@':src,'@hermes/shared':join(workspace,'apps/shared/src')},define:{'import.meta.env':'{}','process.env.NODE_ENV':'"production"','import.meta.env.DEV':'false','import.meta.env.PROD':'true','import.meta.env.BASE_URL':'"/"','import.meta.env.VITE_WEBAPP':'"true"'},loader:{'.woff2':'dataurl','.ttf':'dataurl','.css':'empty'},logLevel:'warning'})
// Vite's catalog artwork glob is unrelated to recorder behavior; emulate its
// empty fixture inventory without changing installed source or loader code.
const bundlePath = join(out,'bundle.js')
writeFileSync(bundlePath, readFileSync(bundlePath,'utf8').replace(/import\.meta\.glob\([\s\S]*?\)/g, '({})'))
const html='<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/desktop.css"></head><body style="margin:0"><div id="root"></div><script type="module" src="/bundle.js"></script></body></html>'
const server=createServer((req,res)=>{const path=req.url.split('?')[0];let body,type='text/html';if(path==='/bundle.js'){body=readFileSync(join(out,'bundle.js'));type='text/javascript'}else if(path==='/plugin.js'){body=readFileSync(process.env.PLUGIN_FILE || join(repo,'desktop/plugin.js'));type='text/javascript'}else if(path==='/desktop.css'){body=readFileSync(join(cssDir,cssName));type='text/css'}else if(path.startsWith('/api/')){res.writeHead(503).end('No backend in fixture');return}else body=html;res.setHeader('Content-Type',type);res.end(body)})
server.listen(Number(process.env.PORT||4197),'127.0.0.1',()=>console.log('SDK proof http://127.0.0.1:'+server.address().port))
