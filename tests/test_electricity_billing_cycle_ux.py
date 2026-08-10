import json
from pathlib import Path

from frontend_runtime import run_node


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "frontend/assets/dashboard_electricity.js"
CSS_PATH = ROOT / "frontend/assets/dashboard_electricity.css"
INDEX_PATH = ROOT / "frontend/index.html"
JS = JS_PATH.read_text(encoding="utf-8")
CSS = CSS_PATH.read_text(encoding="utf-8")
INDEX = INDEX_PATH.read_text(encoding="utf-8")


def ux_runtime():
    source = json.dumps(str(JS_PATH))
    return run_node(
        f"""
const fs=require('fs'),vm=require('vm');
const requests=[];
const document={{readyState:'loading',body:{{appendChild:()=>{{}}}},querySelectorAll:()=>[],querySelector:selector=>selector==='[data-page="electricity"]'?{{}}:null,getElementById:()=>null,createElement:()=>({{dataset:{{}}}})}};
const window={{safeText:String,refresh:async()=>{{}},renderPage:()=>{{}},currentPage:()=> 'overview',nav:()=>{{}},get:async url=>{{
  if(url==='/api/electricity/reconciliation/current') return window.nextReconciliation;
  return {{}};
}}}};
const fetch=async(url,options)=>{{requests.push({{url,method:options.method,body:options.body}});return {{ok:true,json:async()=>({{ok:true}})}};}};
const context={{window,document,console,fetch,URL,URLSearchParams,Intl,Date,FormData,AbortController,Promise,setTimeout,clearTimeout}};
vm.createContext(context);vm.runInContext(fs.readFileSync({source},'utf8'),context);
const api=window.DashboardElectricityHistory;
api.state.billing={{actual_partial_cost:616.19,actual_partial_usage_kwh:168.17,projected_cycle_bill:2709.25,projected_cycle_usage_kwh:605.0087,billing_period_label:'2 Aug 2026 – 1 Sep 2026',coverage:{{missing_start:true}}}};
const closed={{cycle_id:'2026-07-02_2026-08-02',cycle_start:'2026-07-02',cycle_end:'2026-08-02',due_date:'2026-08-13',calculated_cost:1286.86,actual_bill_amount:null,difference_amount:null,difference_percent:null,payment_status:'unpaid',paid_at:null,coverage:{{status:'incomplete',percent:52.55,start_complete:false}}}};
api.state.reconciliation={{active_cycle:{{cycle_end:'2026-09-02',days_until_cycle_end:23,projection_quality:{{start_coverage:'incomplete'}}}},latest_closed_cycle:closed}};
const cycle=api.summaryCards();const daily=api.dailySummaryCards();const absent=api.reconciliationPanel();
api.state.billing.coverage.missing_start=false;api.state.reconciliation.active_cycle.projection_quality.start_coverage='complete';
const completeStartCycle=api.summaryCards();
closed.actual_bill_amount=1300;closed.difference_amount=13.14;closed.difference_percent=1.02;
const entered=api.reconciliationPanel();
closed.coverage={{status:'complete',percent:100,start_complete:true}};
const completeReconciliation=api.reconciliationPanel();
closed.coverage={{status:'unavailable',percent:null,start_complete:null}};
const unavailableReconciliation=api.reconciliationPanel();
const dueSoon=api.dueState({{due_date:'2026-08-13',payment_status:'unpaid'}},'2026-08-10').label;
const dueToday=api.dueState({{due_date:'2026-08-13',payment_status:'unpaid'}},'2026-08-13').label;
const overdue=api.dueState({{due_date:'2026-08-13',payment_status:'unpaid'}},'2026-08-14').label;
window.nextReconciliation={{active_cycle:{{}},latest_closed_cycle:{{...closed,payment_status:'paid',paid_at:'2026-08-12T10:00:00+07:00'}}}};
(async()=>{{
  await api.reconciliationRequest('/api/electricity/reconciliation/2026-07-02_2026-08-02','PUT',{{actual_bill_amount:2872.43}});
  await api.reconciliationRequest('/api/electricity/reconciliation/2026-07-02_2026-08-02/paid','POST');
  await api.reconciliationRequest('/api/electricity/reconciliation/2026-07-02_2026-08-02/unpaid','POST');
  process.stdout.write(JSON.stringify({{cycle,completeStartCycle,daily,absent,entered,completeReconciliation,unavailableReconciliation,dueSoon,dueToday,overdue,requests,state:api.state.reconciliation}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    )


def test_cycle_first_cards_use_authoritative_cycle_fields():
    result = ux_runtime()
    cycle = result["cycle"]
    assert "Current Cycle Cost" in cycle and "฿616.19" in cycle
    assert "2 Aug 2026 – 1 Sep 2026" in cycle
    assert "Projected Bill" in cycle and "฿2,709.25" in cycle
    assert "605.01 kWh projected" in cycle
    assert "Cycle Usage" in cycle and "168.17" in cycle
    assert "Cycle Ends In" in cycle and "23" in cycle and "2 Sept" in cycle
    assert "Estimate · limited data" in cycle
    assert "Estimate · limited data" not in result["completeStartCycle"]
    assert '<em class="electricity-estimate">Estimate</em>' in result["completeStartCycle"]
    assert "estimated_month_end_bill" not in JS


def test_daily_metrics_are_demoted_and_peak_label_is_precise():
    result = ux_runtime()
    assert "Daily Details" in result["daily"]
    assert "Supporting usage context; not the billing-cycle total" in result["daily"]
    assert "Peak Hour Consumption" in result["daily"]
    assert "Today’s Peak" not in JS
    assert JS.index("${summaryCards()}") < JS.index("${dailySummaryCards()}")


def test_reconciliation_absent_entered_difference_variance_and_due_date():
    result = ux_runtime()
    assert "Not entered" in result["absent"]
    assert "Enter Actual Bill" in result["absent"]
    assert "฿1,286.86" in result["absent"]
    assert "Partial data · 52.55% coverage" in result["absent"]
    for value in ("฿1,300.00", "+฿13.14", "+1.02%", "13 Aug 2026", "Unpaid"):
        assert value in result["entered"]
    assert result["entered"].count("Based on partial dashboard data") == 2


def test_complete_and_unavailable_reconciliation_coverage_states():
    result = ux_runtime()
    assert "Partial data" not in result["completeReconciliation"]
    assert "Based on partial dashboard data" not in result["completeReconciliation"]
    unavailable = result["unavailableReconciliation"]
    assert "฿1,286.86" not in unavailable
    assert unavailable.count("Not available") >= 3
    assert "฿0.00" not in unavailable


def test_due_visual_states_and_authoritative_mutation_refresh():
    result = ux_runtime()
    assert (result["dueSoon"], result["dueToday"], result["overdue"]) == ("Due Soon", "Due Today", "Overdue")
    assert [item["method"] for item in result["requests"]] == ["PUT", "POST", "POST"]
    assert json.loads(result["requests"][0]["body"]) == {"actual_bill_amount": 2872.43}
    assert "difference_amount" not in result["requests"][0]["body"]
    assert result["state"]["latest_closed_cycle"]["payment_status"] == "paid"


def test_failure_and_unknown_states_do_not_substitute_zero():
    for text in ("Bill reconciliation is unavailable.", "No closed-cycle reconciliation is available yet.", "Projection unavailable", "—", "Not available"):
        assert text in JS
    assert "actual_partial_cost || 0" not in JS
    assert "projected_cycle_bill || 0" not in JS
    assert "reconciliationMutationError" in JS


def test_responsive_cycle_and_reconciliation_structure_has_no_horizontal_scroll():
    assert ".electricity-cycle-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))" in CSS
    assert "@media(max-width:1180px){.electricity-cycle-summary{grid-template-columns:repeat(2" in CSS
    assert "@media(max-width:560px){.electricity-cycle-summary,.electricity-reconciliation-grid{grid-template-columns:1fr}" in CSS
    assert "overflow-x:hidden" in CSS
    assert "dashboard_electricity_projection.js" not in INDEX


def test_existing_history_and_settings_assets_remain_loaded():
    for asset in ("dashboard_electricity.js", "dashboard_electricity_settings_hotfix17.js", "dashboard_settings.js", "dashboard_polish10.js"):
        assert asset in INDEX
    assert "Consumption History" in JS and "Advanced Diagnostics" in JS
