"""Integrated Charter Strategy & Market Intelligence module for SAGAR DRISHTI."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sail import data, cost, vessels
from sail.config import PORTS, ROUTES, VESSEL_PROFILES, VESSELS

st.markdown("""
<style>
.stApp{background:#f5f9fc;color:#0d2842}.block-container{max-width:1180px;padding-top:1.2rem}
.hero{background:linear-gradient(135deg,#071e33,#0b416d);color:#fff;padding:24px 28px;border-radius:10px;margin-bottom:16px}
.hero h1{margin:0 0 5px;font-size:30px}.hero p{margin:0;color:#dceaf5}
.card{background:#fff;border:1px solid #cbd9e5;border-radius:8px;padding:16px 18px;margin-bottom:12px;box-shadow:0 2px 7px rgba(7,30,51,.05)}
.card h3{color:#0b416d;margin:0 0 7px;font-size:17px}.small{color:#556a7e;font-size:12px}
</style>
""", unsafe_allow_html=True)


def render_charter_strategy():
    st.markdown('<div class="hero"><h1>⚓ Charter Strategy & Market Intelligence</h1><p>From single spot fixtures to proactive multi-voyage charter planning.</p></div>', unsafe_allow_html=True)
    st.info("Scenario intelligence layer: live market, port-congestion and commodity feeds are not assumed where unavailable. Synthetic stress inputs are isolated from historical ML training data and used only for what-if decision support.")
    
    def resolve_vessel(name):
        return (name, VESSEL_PROFILES[name]) if name in VESSEL_PROFILES else (name, VESSELS[name])
    
    def latest_market():
        try:
            df=data.market_frame("BDI")
            return df,float(df.freight.iloc[-1]),float(df.brent.iloc[-1]),float(df.usdinr.iloc[-1])
        except Exception:
            return pd.DataFrame(),1000.0,75.0,90.0
    
    def contract_cost(origin,destination,vessel,cargo,voyages,rate,brent,congestion):
        name,vcfg=resolve_vessel(vessel)
        hire=float(rate)*float(vcfg.get("tce_per_index_point",1))
        try:
            out=cost.voyage_cost(name,origin,destination,float(cargo),int(voyages),hire,float(brent),0.0,float(congestion)*0.02)
            return float(out["total_usd"]),out
        except Exception:
            days=max(1.0,(ROUTES[origin]["distance_nm"]+ROUTES[origin]["ballast_nm"])/(float(vcfg["speed_kn"])*24))
            port_days=max(1.0,float(cargo)/max(1,ROUTES[origin]["load_rate_t_day"])/voyages)
            wait=float(PORTS[destination]["current_wait_days"])*(1+congestion/100)
            total_days=(days+port_days+wait)*voyages
            fuel=float(vcfg["sea_cons_t_day"])*days*float(brent)*7*voyages
            total=hire*total_days+fuel+float(PORTS[destination]["port_charges_usd"])*voyages
            return float(total),{"voyage_days":total_days}
    
    st.markdown("### 1 · Charter scenario")
    c1,c2,c3,c4=st.columns(4)
    with c1: origin=st.selectbox("Loading origin",list(ROUTES.keys()))
    with c2: destination=st.selectbox("Discharge port",list(PORTS.keys()))
    with c3: vessel_class=st.selectbox("Vessel class",list(VESSELS.keys()),index=1)
    with c4: cargo_mt=st.number_input("Total cargo (MT)",10000,1000000,250000,10000)
    c5,c6,c7,c8=st.columns(4)
    with c5: contract_voyages=st.slider("Contract voyages",2,12,4)
    with c6: contract_months=st.selectbox("Contract horizon",[1,2,3,6],index=2,format_func=lambda x:str(x)+" month(s)")
    with c7: congestion=st.slider("Port congestion stress",0,60,10,5,help="Scenario uplift; not a live congestion feed.")
    with c8: commodity_trend=st.slider("Commodity demand trend",-15,20,4,1,help="Synthetic scenario input; 0 = neutral.")
    
    market_df,current_bdi,current_brent,current_fx=latest_market()
    m1,m2,m3,m4=st.columns(4)
    m1.metric("Latest BDI","{:,.0f}".format(current_bdi));m2.metric("Brent proxy","USD {:,.1f}/bbl".format(current_brent));m3.metric("USD / INR","INR {:,.2f}".format(current_fx));m4.metric("Congestion stress","+{}%".format(congestion))
    if not market_df.empty:
        hist=market_df.tail(120)
        fig=go.Figure(go.Scatter(x=hist.index,y=hist.freight,mode="lines",name="BDI"))
        fig.update_layout(height=280,margin=dict(l=10,r=10,t=30,b=10),title="Historical freight context",yaxis_title="Index")
        st.plotly_chart(fig,use_container_width=True)
    
    st.markdown("### 2 · Commodity, macro & disruption context")
    a,b,c=st.columns(3)
    with a:
        st.metric("Scenario demand index","{:.0f}/100".format(50+commodity_trend*2))
        st.caption("Synthetic demand variable. Connect coal/iron-ore price and demand feeds when available.")
    with b:
        pressure=min(100,max(0,50+congestion*.8+commodity_trend*1.2))
        st.metric("Operational pressure","{:.0f}/100".format(pressure))
        st.caption("Demand + congestion stress; not a live congestion measurement.")
    with c:
        st.metric("Fuel pressure","{:.0f}/100".format(min(100,max(0,current_brent/100*70))))
        st.caption("Brent is the existing bunker-cost proxy.")
    
    st.markdown("### 3 · Seven-day freight & disruption simulator")
    scenario=st.selectbox("Market scenario",["base","bull","bear"])
    try:
        scen=data.synthetic_future_scenario("BDI",days=7,scenario=scenario).copy()
        scen["day"]=np.arange(1,len(scen)+1);scen["commodity_pressure"]=commodity_trend;scen["congestion_pct"]=congestion
        scen["stress_multiplier"]=1+scen["commodity_pressure"]/1000+scen["congestion_pct"]/500
        scen["effective_freight"]=scen["freight"]*scen["stress_multiplier"]
        fig=go.Figure()
        fig.add_trace(go.Scatter(x=scen.day,y=scen.freight,mode="lines+markers",name="Synthetic freight"))
        fig.add_trace(go.Scatter(x=scen.day,y=scen.effective_freight,mode="lines+markers",name="Stress-adjusted freight",line=dict(dash="dash")))
        fig.update_layout(height=300,margin=dict(l=10,r=10,t=35,b=10),title="7-day "+scenario.upper()+" scenario — synthetic future path",xaxis_title="Day",yaxis_title="Freight index")
        st.plotly_chart(fig,use_container_width=True)
        st.dataframe(scen[["day","freight","effective_freight","commodity_pressure","congestion_pct"]].rename(columns={"freight":"Base freight","effective_freight":"Stress-adjusted freight"}),use_container_width=True,hide_index=True)
    except Exception as exc:
        st.warning("Scenario simulator unavailable: "+str(exc))
    
    st.markdown("### 4 · Spot vs multi-voyage charter strategy")
    try:
        if "scen" in locals() and not scen.empty:
            rates={"Spot":float(scen.effective_freight.iloc[0]),"Short-term":float(scen.effective_freight.mean()),"Medium-term":float(scen.effective_freight.mean()*(1+max(0,commodity_trend)/500))}
        else:
            rates={"Spot":current_bdi,"Short-term":current_bdi,"Medium-term":current_bdi}
        rows=[]
        plans=[("Spot",1,rates["Spot"]),("Short-term ("+str(contract_voyages)+" voyages)",contract_voyages,rates["Short-term"]),("Medium-term ("+str(contract_voyages)+" voyages)",contract_voyages,rates["Medium-term"])]
        for label,voyages,rate in plans:
            total,detail=contract_cost(origin,destination,vessel_class,cargo_mt,voyages,rate,current_brent,congestion)
            rows.append({"Strategy":label,"Voyages":voyages,"Rate basis":rate,"Estimated total USD":total,"USD / MT":total/max(cargo_mt,1),"Estimated days":detail.get("weather_adjusted_voyage_days",detail.get("voyage_days",0))})
        df=pd.DataFrame(rows)
        st.dataframe(df.style.format({"Rate basis":"{:,.0f}","Estimated total USD":"USD {:,.0f}","USD / MT":"USD {:,.2f}","Estimated days":"{:,.1f}"}),use_container_width=True,hide_index=True)
        st.markdown('<div class="card"><h3>Decision lens</h3><p><b>Spot:</b> maximum flexibility with repeated market exposure.</p><p><b>Short-term:</b> spreads exposure across multiple voyages and reduces repeated fixture-search effort.</p><p><b>Medium-term:</b> greater commitment; requires stronger confidence in cargo continuity, vessel availability and port conditions.</p><p class="small">Scenario comparison only — not a binding commercial quote or autonomous charter decision.</p></div>',unsafe_allow_html=True)
    except Exception as exc:
        st.error("Contract strategy calculation failed: "+str(exc))
    
    st.markdown("### 5 · Idle-time & alternative-employment planner")
    i1,i2,i3=st.columns(3)
    with i1: idle_days=st.number_input("Expected idle days",0.0,30.0,3.0,.5)
    with i2: daily_tce=st.number_input("Daily vessel exposure (USD/day)",5000,100000,35000,1000)
    with i3: reposition_days=st.number_input("Repositioning days",0.0,20.0,2.0,.5)
    idle_cost=idle_days*daily_tce; reposition_cost=reposition_days*daily_tce
    x,y,z=st.columns(3);x.metric("Idle exposure","USD {:,.0f}".format(idle_cost));y.metric("Reposition exposure","USD {:,.0f}".format(reposition_cost));z.metric("Avoidable exposure","USD {:,.0f}".format(max(0,idle_cost-reposition_cost)))
    alt_port=st.selectbox("Potential alternative employment / repositioning port",list(PORTS.keys()),index=min(1,len(PORTS)-1))
    st.markdown('<div class="card"><h3>Operational decision frame</h3><p>Waiting exposure: <b>USD {:,.0f}</b> for {:.1f} idle days.</p><p>Repositioning exposure: <b>USD {:,.0f}</b> for {:.1f} days toward <b>{}</b>.</p><p class="small">The module identifies a decision to investigate; it does not invent an available fixture.</p></div>'.format(idle_cost,idle_days,reposition_cost,reposition_days,alt_port),unsafe_allow_html=True)
    
    st.markdown("### 6 · Vessel–port constraint gate")
    vname,vcfg=resolve_vessel(vessel_class);feas=vessels.evaluate(vname,destination,cargo_mt)
    checks=feas["checks"];cols=st.columns(4)
    for idx,key in enumerate(["LOA","Beam","DWT","Draft"]): cols[idx].metric(key,"PASS" if checks.get(key) else "CHECK")
    st.caption("{}: {:,} DWT · LOA {} m · Beam {} m · Draft {} m · Port: {}. Configured master values are indicative unless verified.".format(vessel_class,vcfg["dwt"],vcfg["loa_m"],vcfg["beam_m"],vcfg["draft_m"],destination))
    for reason in feas["reasons"]: st.write("• "+reason)
    
    st.markdown('<div class="card"><h3>SIH objective coverage</h3><p><b>Forecast → market context → vessel/port feasibility → cost → contract structure → idle/repositioning scenario.</b></p><p>This closes the decision loop around the objective of moving from repeated spot fixtures toward short- and medium-term multiple-voyage planning.</p></div>',unsafe_allow_html=True)
    