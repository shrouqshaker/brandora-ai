"""Offline cross-language contract check against an explicit local Backend build.

No environment files, SQL, real credentials, databases, live LLMs or WhatsApp.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.offline_demo import OfflineHarness
from app.schemas.backend import BusinessInformation


def verify(backend_root):
    backend_root = Path(backend_root).resolve(strict=True)
    if not (backend_root / 'dist/ai/ai-response.js').is_file():
        raise ValueError('Build the intended local Backend before verifying contracts')
    h = OfflineHarness()
    examples = []
    for text in ['what is your store name?', 'I want 2 black tshirt size L', 'can I get a discount?', 'remove all items', 'never mind']:
        response = h.send(h.body(text))
        if response.status_code != 200:
            raise AssertionError('Synthetic AI response failed')
        examples.append(response.json())
    source = r"""
const fs = require('node:fs'), path = require('node:path');
const root = process.argv[1];
const {validateAiResponse} = require(path.join(root,'dist/ai/ai-response.js'));
const values = JSON.parse(fs.readFileSync(0,'utf8'));
for (const value of values) {
  const normalized = validateAiResponse(value);
  if (JSON.stringify(normalized.orderCandidate)!==JSON.stringify(value.orderCandidate))
    throw new Error('Backend silently rejected an AI suggestion');
}
const {BusinessesService}=require(path.join(root,'dist/businesses/businesses.service.js'));
const {AiService}=require(path.join(root,'dist/ai/ai.service.js'));
const {digest}=require(path.join(root,'dist/ai/ai-security.js'));
const id='10000000-0000-4000-8000-000000000001';
const pick=(row,select)=>Object.fromEntries(Object.keys(select).map(k=>[k,row[k]]));
const profile={id,name:'Synthetic Store',description:'Handmade gifts',category:'Handmade',phone:null,address:null,logoUrl:null,createdAt:new Date(),updatedAt:new Date(),logoPublicId:'private-never-return'};
const settings={businessId:id,acceptsCash:true,acceptsOnlinePayment:false,deliveryEnabled:true,pickupEnabled:true,returnPolicy:'7 days only',paymentNotes:'Cash on delivery',deliveryNotes:'Owner-specified delivery areas',deliveryFee:'0.00'};
const faq={id, businessId:id,question:'Gift wrapping?',answer:'Ask the owner',position:0,isActive:true};
const db={business:{findUnique:async({select})=>pick(profile,select)},businessSettings:{findUnique:async({select})=>pick(settings,select)},workingHour:{findMany:async()=>[]},businessFaq:{findMany:async({where,select})=>where.businessId===id&&where.isActive===true?[pick(faq,select)]:[]}};
const businesses=new BusinessesService(db,{business:async(bid)=>{if(bid!==id)throw new Error('scope');}});
const current={businessId:id,customerId:id,conversationId:id,messageId:id,message:'Synthetic question',createdAt:new Date().toISOString(),senderType:'CUSTOMER',messageType:'TEXT'};
const hash=digest(JSON.stringify({businessId:id,customerId:id,conversationId:id,messageId:id,message:current.message}));
const service=new AiService({}, {current:async()=>current}, {}, {}, businesses);
service.tool({...current,requestHash:hash},{tool:'get_business_info',arguments:{}}).then(result=>{
 if(JSON.stringify(result).includes('private-never-return'))throw new Error('Private profile leaked');
 process.stdout.write(JSON.stringify(result));
}).catch(()=>{process.stderr.write('Synthetic Backend contract check failed');process.exitCode=1;});
"""
    result = subprocess.run(['node', '-e', source, str(backend_root)], input=json.dumps(examples),
                            encoding='utf-8', capture_output=True, timeout=30, check=True)
    info = BusinessInformation.model_validate(json.loads(result.stdout)['data'])
    assert info.business.name == 'Synthetic Store'
    assert info.settings.deliveryFee == 0 and info.settings.returnPolicy == '7 days only'
    assert len(info.faqs) == 1
    assert examples[1]['orderCandidate']['items'][0]['quantity'] == 2
    assert examples[2]['orderCandidate']['discountRequest']
    print(json.dumps({'offline': True, 'aiResponsesAcceptedByBackend': len(examples),
                      'actualBackendToolAggregateAcceptedByAI': True, 'liveProvidersTested': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend-root', required=True)
    verify(parser.parse_args().backend_root)
