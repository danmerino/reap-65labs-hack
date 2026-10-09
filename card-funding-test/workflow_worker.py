"""Execute fixed demo jobs; only the user's Pay/Fund clicks can submit financial writes."""
import contextlib
import fcntl
import io
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import time
import app
from export_status import observe, save, payment_view

sys.path.insert(0, str(app.ROOT.parent / 'scout'))
import agent_flow as flow
import inventory

NODE = app.ROOT.parent / 'durable-demo/.runtime/node_modules/.bin/node'
DIALOGUE = app.ROOT.parent / 'durable-demo/scout-dialogue.mjs'
SUBSTITUTE = {'product_id':'prd_66cf7b822c42428988a5437ac787b093',
              'title':'It Girl Sticker Pack','merchant':'EVRYJEWELS',
              'variant_id':'var_b2ba472017964febae8a15e92a2458a1'}
MAX_DEMO_UNITS = 10_000_000


class QuoteBudgetError(ValueError):
    pass


def talk(role, key, facts):
    actor = 'Europe agent' if role == 'buyer' else 'Funding agent'
    recipient = 'Funding agent' if role == 'buyer' else 'Europe agent'
    result = subprocess.run([str(NODE), str(DIALOGUE)], input=json.dumps({
        'role': role, 'message': json.dumps({'recipient': recipient, 'verified_facts': facts}),
        'request_id': key}), text=True, capture_output=True, timeout=75)
    try:
        answer = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        answer = {}
    if result.returncode or not answer.get('text'):
        flow.event(key+':unavailable', 'Pi runtime', 'agent_unavailable',
                   'The Pi agent response is unavailable. No AI message was fabricated.', recipient=recipient)
        return None
    flow.event(key, actor, 'message', answer['text'], recipient=recipient, source='pi_ai',
               facts={'runtime': answer['runtime'], 'model': answer['model']})
    return answer['text']


def quote():
    creds = app.session()
    saved = flow.state().get('quote')
    attempts = app.kwal.AttemptStore(app.PRIVATE / 'kwal-credentials.json').load()
    if saved and any(a.quote_id == saved['quote_id'] for a in attempts):
        return saved
    if saved:
        candidate = app.kwal.read_quote(app.KWAL, creds.token, saved['quote_id'])
        if not candidate.is_expired(int(time.time())):
            verify_quote(creds, candidate)
            return saved
    if not saved:
        selected = dict(SUBSTITUTE)
    else:
        selected = {key: saved[key] for key in ('product_id', 'title', 'merchant', 'variant_id')}
    candidate = app.kwal.create_quote(app.KWAL, creds.token,
        (app.kwal.QuoteLine(selected['variant_id'], 1),), email='scout-demo@example.com',
        shipping_address={'firstName':'Demo','lastName':'Buyer','phone':'+12025550123',
                          'line1':'123 Example Street','city':'San Francisco','region':'CA',
                          'postalCode':'94105','country':'US'})
    flow.update(substitute_candidate=selected)
    required = verify_quote(creds, candidate)
    from decimal import Decimal
    price = format(Decimal(required)/1_000_000, '.6f')
    selected.update(quote_id=candidate.quote_id, price_test_usdc=price,
                    amount_minor_units=required, expires_at=candidate.expires_at)
    flow.update(quote=selected)
    flow.event('quote:'+candidate.quote_id, 'Kwal', 'quote',
               'Fixed sandbox substitute quoted at '+price+' test USDC including shipping and tax.', source='kwal_api',
               facts={'quote_id': candidate.quote_id, 'substitute': selected['title'], 'amount_test_usdc': price})
    return selected


def verify_quote(creds, candidate):
    # A single hardcoded substitute, fixed quantity, sandbox gateway and bounded lab budget.
    if (candidate.payment_id
            or len(candidate.lines) != 1 or candidate.lines[0].quantity != 1
            or candidate.needs_shipping_selection() or candidate.is_expired(int(time.time()))):
        raise ValueError('Not a fresh one-item local sandbox quote')
    funds = app.kwal.read_funding(app.KWAL, creds.token, quote_id=candidate.quote_id)
    if funds.required and funds.required.currency == 'USDC' and funds.required.decimals == 6 and funds.required.minor_units > MAX_DEMO_UNITS:
        from decimal import Decimal
        price = format(Decimal(funds.required.minor_units)/1_000_000, '.6f')
        flow.update(observed_quote={'quote_id':candidate.quote_id,'price_test_usdc':price})
        flow.event('over-budget:'+candidate.quote_id, 'Kwal', 'quote_over_budget',
                   'Provider quote is '+price+' test USDC, above the bounded sandbox lab budget. Payment is blocked.',
                   source='kwal_api', facts={'quote_id':candidate.quote_id,'amount_test_usdc':price})
        raise QuoteBudgetError('Provider quote exceeds the sandbox lab limit. Payment has not been submitted.')
    if (not funds.required or funds.required.currency != 'USDC' or funds.required.decimals != 6
            or not 0 < funds.required.minor_units <= MAX_DEMO_UNITS or not funds.quote
            or funds.quote.token.chain_id != 763373
            or funds.quote.token.contract_address.lower() != app.TEST_USDC.lower()
            or funds.quote.merchant_total != candidate.total or funds.state not in ('ready', 'funds_needed')):
        raise ValueError('The sandbox quote funding amount is not verified')
    return funds.required.minor_units


def refresh():
    data = observe();save(data)
    return data


def cycle_key(name):
    return flow.state()['cycle_id']+':'+name


def deposit_path():
    return app.PRIVATE / ('scout-'+flow.state()['cycle_id']+'-deposit.json')


def prepare():
    flow.update(stage='discovering', error=None)
    selected = quote()
    data = refresh()
    buyer_request = talk('buyer', cycle_key('fixed-discovery'), {
        'demo_inventory':'One matcha watch found in the demo supplier JSON.',
        'verified_total_test_usdc':selected['price_test_usdc'],
        'card_spendable_test_usdc':data['card']['spendable_test_usdc'],
        'request':'Ask the funding agent to cover only the shortfall for the fixed sandbox purchase. No purchase has been paid yet.'})
    required = selected['amount_minor_units']
    ready = data['card']['spendable_test_usdc'] is not None and int(Decimal(data['card']['spendable_test_usdc'])*1_000_000) >= required
    if ready:
        talk('treasury', cycle_key('prefunded'), {'buyer_message': buyer_request, 'card_spendable_test_usdc': data['card']['spendable_test_usdc'],
            'vault': data['vault']['address'], 'instruction': 'Tell the buyer it is already funded; no new transfer is needed. Payment has not been submitted.'})
        flow.event(cycle_key('funding-skipped'), 'Funding agent', 'funding_ready',
                   'Existing card capacity covers the quote. Skipping a new deposit.', source='kwal_api')
    else:
        shortfall = max(Decimal(0), Decimal(selected['price_test_usdc'])-Decimal(data['card']['spendable_test_usdc'] or '0'))
        talk('treasury',cycle_key('fixed-funding-needed'),{'buyer_message':buyer_request,
            'verified_total_test_usdc':selected['price_test_usdc'],
            'card_spendable_test_usdc':data['card']['spendable_test_usdc'],
            'shortfall_test_usdc':str(shortfall),
            'instruction':'Tell the buyer the additional funding needed. The operator must click Fund; no transfer has been sent.'})
    flow.update(stage='ready' if ready else 'needs_funding')


def fund():
    selected = quote()
    data = refresh()
    required = selected['amount_minor_units']
    available = int(Decimal(data['card']['spendable_test_usdc'] or '0')*1_000_000)
    shortfall = max(0, required-available)
    if not shortfall:
        flow.event(cycle_key('funding-skipped'), 'Funding agent', 'funding_ready',
                   'Already funded. No additional transfer submitted.', source='kwal_api')
        flow.update(stage='ready', error=None)
        return
    saved_deposit = deposit_path()
    if saved_deposit.is_file():
        record = json.loads(saved_deposit.read_text())
        result = app.fund(record['amount_minor_units'], True, record_path=saved_deposit,
                          required_capacity=required)
        flow.update(stage='funding',funding_transaction=result.get('transaction_hash'))
        flow.event(cycle_key('fund-reconcile'),'Funding controller','funding_processing',
                   'Checking the existing funding transfer. No replacement transfer is submitted.',
                   facts={'transaction_hash':result.get('transaction_hash')})
        for _ in range(20):
            current = refresh()
            if Decimal(current['card']['spendable_test_usdc'] or '0')*1_000_000 >= required:
                flow.update(stage='ready',error=None)
                return
            time.sleep(3)
        raise ValueError('Existing funding remains unconfirmed')
    price = str(Decimal(shortfall)/1_000_000)
    buyer_request = talk('buyer', cycle_key('fund-request'), {'request': 'Request only the missing funds for the verified quote.',
        'quote_id': selected['quote_id'], 'vault': data['vault']['address'], 'shortfall_test_usdc': price})
    talk('treasury', cycle_key('fund-intent'), {'buyer_message': buyer_request, 'shortfall_test_usdc':price,
                                       'instruction': 'Acknowledge the exact funding request. Transfer has not been sent yet.'})
    flow.update(stage='funding')
    result = app.fund(shortfall, True, record_path=deposit_path(), required_capacity=required)
    if result.get('state') == 'already_sufficient':
        flow.event(cycle_key('funding-skipped'),'Funding agent','funding_ready',
                   'The card is already fully funded. No additional transfer was submitted.',source='kwal_api')
        refresh()
        flow.update(stage='ready',error=None)
        return
    flow.update(funding_transaction=result.get('transaction_hash'))
    flow.event(cycle_key('fund-submit'), 'Funding controller', 'funding_submitted',
               ('Observing the existing ' if result.get('replayed') else 'Submitted ')+price+'-test-USDC transfer to this agent’s verified vault.',
               facts={'transaction_hash': result.get('transaction_hash'), 'to': data['vault']['address'], 'amount_test_usdc': price})
    for _ in range(20):
        time.sleep(3)
        current = refresh()
        if float(current['card']['spendable_test_usdc'] or 0)*1_000_000 >= required:
            flow.update(stage='ready', error=None)
            talk('treasury', cycle_key('fund-confirmed'), {'card_spendable_test_usdc': current['card']['spendable_test_usdc'],
                'transaction_hash': result.get('transaction_hash'), 'instruction': 'Report provider-confirmed funding to the buyer.'})
            return
    raise ValueError('Funding confirmation remains pending; preserve the existing transfer')


def payment_update(payment):
    value = payment_view(payment)
    value['quote_id'] = flow.state()['quote']['quote_id']
    flow.record_payment(value)


def pay():
    selected = quote()
    prior = app.kwal.AttemptStore(app.PRIVATE / 'kwal-credentials.json').find(selected['quote_id'])
    if not prior:
        data = refresh()
        if float(data['card']['spendable_test_usdc'] or 0)*1_000_000 < selected['amount_minor_units']:
            raise ValueError('Card funding is not ready')
        talk('buyer', cycle_key('pay-intent'), {'instruction': 'Tell the funding agent you are about to submit the approved sandbox purchase. It has not been paid yet.',
             'quote_id': selected['quote_id'], 'amount_test_usdc': selected['price_test_usdc'], 'substitute': selected['title']})
    flow.update(stage='submitted')
    # Keep CLI JSON private to this process; only the explicit event projection is published.
    with contextlib.redirect_stdout(io.StringIO()):
        payment = app.purchase(selected['quote_id'], selected['amount_minor_units'], on_payment=payment_update)
    data = refresh()
    if flow.state()['stage'] == 'settled':
        talk('buyer', cycle_key('paid'), {'payment_id': payment.payment_id, 'card_transaction_id': payment.card_transaction_id,
            'charged_test_usdc': selected['price_test_usdc'], 'settlement': 'confirmed', 'demo_inventory': 'One demo matcha-watch unit added; no physical goods ship.',
            'instruction': 'Report the confirmed sandbox payment and demo listing to the funding agent.'})
    elif payment.state == 'pending':
        flow.update(stage='reconcile', error='Payment is still processing. Check the same payment; do not start another purchase.')


def run():
    flow.init()
    with flow.db() as c:
        # An interrupted write must be reconciled through its existing payment/deposit records.
        interrupted = list(c.execute("SELECT id,action FROM jobs WHERE status='running'"))
        c.execute("UPDATE jobs SET status='blocked' WHERE status='running'")
    if interrupted:
        flow.update(stage='quote_needed' if all(j['action']=='prepare' for j in interrupted) else 'reconcile',
                    error='An interrupted action needs reconciliation using its existing records.')
    while True:
        job = flow.claim()
        if not job:
            time.sleep(1)
            continue
        job_key = cycle_key('job:'+str(job['id'])+':'+str(job['attempt']))
        flow.event(job_key, 'Demo operator', 'action', 'Requested '+job['action']+'.')
        try:
            {'prepare': prepare, 'fund': fund, 'pay': pay}[job['action']]()
            flow.finish(job['id'])
        except Exception as error:
            # Never expose provider bodies, session values, or subprocess stderr.
            flow.event(job_key+':error', 'Controller', 'action_blocked',
                       'The action stopped before a confirmed result. Existing records are retained.',
                       facts={'error_type': type(error).__name__})
            flow.update(stage='reconcile' if job['action'] in ('fund','pay') else 'quote_needed',
                        error=str(error) if isinstance(error,QuoteBudgetError) else 'The sandbox action needs another check. No replacement payment or transfer was submitted.')
            if job['action']=='fund':
                saved = deposit_path()
                if saved.is_file():
                    flow.update(funding_transaction=json.loads(saved.read_text()).get('transaction_hash'))
            flow.finish(job['id'], 'blocked')


if __name__ == '__main__':
    flow.DATA.mkdir(parents=True, exist_ok=True)
    with (flow.DATA / 'agent-worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run()
