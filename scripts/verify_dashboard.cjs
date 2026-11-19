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
  for(const name of ['Incidents','AI Impact','Provisioning']) {
   await page.getByRole('button',{name,exact:true}).click();
   await page.getByRole('heading',{name,exact:true}).waitFor();
   if(name==='Incidents'&&await page.getByRole('textbox',{name:'Ask NetGuard'}).isVisible())throw Error('Chat repeated on incident page');
   await page.screenshot({path:'docs/proof/p3/'+name.toLowerCase().replace(' ','-')+'.png',fullPage:true});
  }
  const field=page.getByRole('textbox',{name:'Ask NetGuard'});
  await field.fill('Add a host under Leaf 2.');
  const response=page.waitForResponse(r=>r.url().endsWith('/api/operator'),{timeout:60000});
  await field.press('Enter');
  const data=await(await response).json();
  if(!data.plan)throw Error(JSON.stringify(data));
  await page.getByRole('heading',{name:'Proposed',exact:true}).waitFor();
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
  await page.getByRole('button',{name:'Command Center',exact:true}).click();
  await page.screenshot({path:'docs/proof/p3/mobile.png',fullPage:true});
  if(errors.length)throw Error(errors.join('\n'));
  console.log('Four workspaces, topology preview, approval, verification, rollback and mobile rendering passed.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
