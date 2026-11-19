// Run with NODE_PATH pointing to a local playwright-core installation.
const {chromium}=require('playwright-core');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const errors=[];
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:3001');
  await page.getByRole('heading',{name:'Live Network Topology'}).waitFor();
  await page.locator('.react-flow__node').filter({hasText:'Spine 1'}).waitFor();
  await page.screenshot({path:'docs/proof/p3/command-center.png',fullPage:true});
  const nav=page.getByRole('navigation',{name:'Workspace navigation'});
  for(const link of await nav.getByRole('link').all()) {
   const response=await page.request.get('http://127.0.0.1:3001'+await link.getAttribute('href'));
   if(!response.ok()||!response.headers()['content-type']?.startsWith('text/html'))throw Error('Workspace link did not serve HTML: '+response.url());
  }
  const field=page.getByRole('textbox',{name:'Ask NetGuard'});
  await page.getByRole('button',{name:'Open AI Agent',exact:true}).click();
  await page.waitForURL('**/agent/');
  await page.getByRole('heading',{name:'AI Agent',exact:true}).waitFor();
  await field.fill('Draft to preserve');
  await page.goBack();
  await page.getByRole('heading',{name:'Command Center',exact:true}).waitFor();
  if(await field.inputValue()!=='Draft to preserve')throw Error('Workspace navigation cleared the draft');
  await page.goForward();
  await page.getByRole('heading',{name:'AI Agent',exact:true}).waitFor();
  await field.clear();
  for(const name of ['Incidents','AI Impact','Provisioning']) {
   await nav.getByRole('link',{name,exact:true}).click();
   await page.getByRole('heading',{name,exact:true}).waitFor();
   if(name==='Incidents') {
    await page.getByPlaceholder('Find or inspect an incident…').waitFor();
    await page.getByRole('button',{name:'Recent incidents',exact:true}).click();
    await page.getByRole('log',{name:'Chat history'}).getByText(/Found \d+ matching journal records?|No recorded incidents or investigations match/).waitFor({timeout:60000});
    const result=page.getByRole('region',{name:'Incident search results'}).getByRole('button',{name:/^Inspect /}).first();
    if(await result.count()) {
     await result.click();
     await page.getByRole('region',{name:'Incident inspection'}).getByText('Full journal log',{exact:true}).waitFor();
    }
   }
   await page.screenshot({path:'docs/proof/p3/'+name.toLowerCase().replace(' ','-')+'.png',fullPage:true});
  }
  const chat=page.getByRole('log',{name:'Chat history'});
  const guide=page.getByRole('button',{name:'How to use NetGuard Agent'});
  await guide.scrollIntoViewIfNeeded();
  const composer=page.locator('.chat-composer');
  const before=await composer.boundingBox();
  await guide.click();
  await page.getByRole('dialog',{name:'How to use NetGuard Agent'}).waitFor();
  const during=await composer.boundingBox();
  if(before.y!==during.y||before.height!==during.height)throw Error('Opening the guide moved the composer');
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'Show impact',exact:true}).click();
  await chat.getByText('Measured workflow outcomes from the lab.',{exact:true}).waitFor();
  await field.fill('Add a host under Leaf 2.');
  await field.press('Enter');
  await chat.getByRole('button',{name:'Approve deployment'}).waitFor({timeout:60000});
  await chat.getByText('Build a topology preview',{exact:true}).waitFor();
  await chat.getByText('Measured workflow outcomes from the lab.',{exact:true}).waitFor();
  await chat.getByText('Topology comparison',{exact:true}).click();
  await chat.getByRole('heading',{name:'Proposed',exact:true}).waitFor();
  await page.screenshot({path:'docs/proof/p3/provision-preview.png',fullPage:true});
  try {
   const deploy=page.waitForResponse(r=>r.url().endsWith('/approve'),{timeout:180000});
   await page.getByRole('button',{name:'Approve deployment'}).click();
   if(!(await deploy).ok())throw Error('Deployment rejected');
   await page.getByRole('button',{name:'Roll back this change'}).waitFor({timeout:15000});
   const current=await page.evaluate(async()=>await(await fetch('/api/provision')).json());
   if(current.phase!=='verified')throw Error(JSON.stringify(current));
   console.log('Browser approved and verified',current.added);
  } finally {
   const rollback=page.waitForResponse(r=>r.url().endsWith('/rollback'),{timeout:180000});
   await page.getByRole('button',{name:'Roll back this change'}).click();
   if(!(await rollback).ok())throw Error('Rollback failed');
  }
  await page.setViewportSize({width:390,height:844});
  await nav.getByRole('link',{name:'Overview',exact:true}).click();
  await page.screenshot({path:'docs/proof/p3/mobile.png',fullPage:true});
  if(errors.length)throw Error(errors.join('\n'));
  console.log('Five workspaces, browser history, shared chat, fixed composer, topology preview, approval, verification, rollback and mobile rendering passed.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
