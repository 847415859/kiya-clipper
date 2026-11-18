import React from 'react';
import {AbsoluteFill, Img, useVideoConfig} from 'remotion';
import {scheduleLayout} from '../schedule-layout.js';

const PURPLE = '#68557e';
const weekdays = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];
const shortDate = value => value.slice(5).replace('-', '.');
const clipped = (text, length) => Array.from(text).slice(0, length).join('') + (Array.from(text).length > length ? '…' : '');

const Heart = ({style}) => <svg viewBox="0 0 100 100" style={style} fill="none"><path d="M50 86C-8 48 10 4 39 25L50 40 61 25C91-2 112 48 50 86Z" stroke="#bc80b3" strokeWidth="5" strokeLinecap="round" /></svg>;
const Brush = () => <svg viewBox="0 0 180 82" style={{position:'absolute',inset:0,width:'100%',height:'100%'}}><path d="M9 11L163 6 176 18 159 26 176 35 163 44 177 58 161 64 168 75 9 76 17 62 2 57 15 45 4 34 18 27Z" fill="#d7c9e5"/></svg>;

const Portrait = ({schedule, assetBase}) => {
  const image = schedule.portrait && `${assetBase || ''}/${encodeURI(schedule.portrait)}`;
  return <div style={{position:'absolute',left:88,top:105,width:630,height:850,transform:'rotate(-4deg)',
    background:'#faf5fd',border:'12px solid #aa8aba',boxShadow:'8px 16px 22px #42344e55',boxSizing:'border-box'}}>
    <div style={{position:'absolute',left:13,top:13,width:580,height:657,overflow:'hidden',background:'#eadbf0'}}>
      {image ? <Img src={image} style={schedule.portrait_crop==='weekly-left' ?
        {position:'absolute',width:2304,height:1296,maxWidth:'none',left:-204,top:-210} :
        {width:'100%',height:'100%',objectFit:'cover',objectPosition:'center'}} /> :
        <div style={{height:'100%',display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',
          background:'radial-gradient(circle at 40% 30%,#fcf0fa,#d7c3e6)',color:PURPLE}}>
          <Heart style={{width:180,height:180}}/><div style={{fontSize:55,marginTop:28}}>下次直播见</div>
        </div>}
    </div>
    <div style={{position:'absolute',top:684,width:'100%',textAlign:'center',fontSize:23,letterSpacing:8,color:'#b097c3'}}>STREAM DIARY</div>
    <div style={{position:'absolute',left:-34,top:724,width:674,height:112,background:'#ead0f6',
      clipPath:'polygon(0 0,100% 0,97% 50%,100% 100%,0 100%,2% 50%)',boxShadow:'3px 6px 8px #60516b50',
      display:'flex',alignItems:'center',justifyContent:'center',fontSize:56,fontWeight:600,color:PURPLE}}>{clipped(schedule.streamer_name || '主播',12)}</div>
    <svg viewBox="0 0 72 160" style={{position:'absolute',left:-32,top:681,width:55,height:120,transform:'rotate(-23deg)'}} fill="none">
      <path d="M51 118V28C51 1 12 1 12 28V132C12 158 62 158 62 129V34" stroke="#7c638e" strokeWidth="5"/><path d="M25 26V124C25 140 47 140 47 124" stroke="#cda4d4" strokeWidth="5"/>
    </svg>
  </div>;
};

export const ScheduleEndCard = ({schedule, assetBase}) => {
  const {width,height} = useVideoConfig();
  const k = Math.min(width/1920,height/1080), layout = scheduleLayout(schedule.entries);
  const end=new Date(schedule.week_start+'T00:00:00Z');end.setUTCDate(end.getUTCDate()+6);
  const weekLabel=shortDate(schedule.week_start)+' — '+shortDate(end.toISOString().slice(0,10));
  return <AbsoluteFill style={{background:'#ded4e6',fontFamily:'"Noto Sans CJK SC","Microsoft YaHei",sans-serif',color:PURPLE}}>
    <div style={{position:'absolute',left:'50%',top:'50%',width:1920,height:1080,transform:`translate(-50%,-50%) scale(${k})`}}>
      <div style={{position:'absolute',inset:24,borderRadius:18,background:'#75648e',boxShadow:'0 9px 22px #3e304d70'}}/>
      <div style={{position:'absolute',inset:43,borderRadius:12,border:'3px dashed #c3b4d4'}}/>
      <div style={{position:'absolute',left:64,top:68,width:1792,height:944,border:'5px solid #c4b0d4',boxSizing:'border-box',
        backgroundColor:'#fcfafc',backgroundImage:'linear-gradient(#ad99bc13 2px,transparent 2px),linear-gradient(90deg,#ad99bc13 2px,transparent 2px)',backgroundSize:'52px 52px'}}/>
      {!layout.dense && <><div style={{position:'absolute',left:90,top:86,width:638,height:880,transform:'rotate(-9deg)',background:'repeating-linear-gradient(0deg,#d1c6dc,#d1c6dc 4px,#d7cde0 4px,#d7cde0 8px)',boxShadow:'3px 6px 12px #53426045'}}/>
      <div style={{position:'absolute',left:157,top:74,width:247,height:65,transform:'rotate(-13deg)',background:'#ccb9dcaa',borderLeft:'3px dashed #f3e9fa',borderRight:'3px dashed #f3e9fa'}}/>
      <Portrait schedule={schedule} assetBase={assetBase}/>
      <Heart style={{position:'absolute',left:633,top:66,width:110,height:110,transform:'rotate(18deg)'}}/></>}
      {layout.dense && <><div style={{position:'absolute',left:105,top:87,width:225,height:162,overflow:'hidden',transform:'rotate(-4deg)',border:'7px solid #c4a8d4',background:'#eadbf0'}}>
        <div style={{position:'absolute',left:-25,top:-28,transform:'scale(.39)',transformOrigin:'top left'}}><Portrait schedule={schedule} assetBase={assetBase}/></div>
      </div><div style={{position:'absolute',left:373,top:222,fontSize:26,color:'#9679a8'}}>{schedule.streamer_name || '主播'}</div></>}
      <div style={{position:'absolute',left:layout.dense?377:798,top:layout.dense?99:110,fontSize:24,letterSpacing:6,color:'#b49bc4'}}>WEEKLY STREAM DIARY</div>
      <div style={{position:'absolute',left:layout.dense?370:790,top:layout.dense?139:150,fontSize:64,fontWeight:600,letterSpacing:5}}>本周后续日程</div>
      <div style={{position:'absolute',left:1400,width:400,top:99,textAlign:'right',color:'#a387b8',fontSize:27,lineHeight:1.7}}>{weekLabel}<br/>北京时间 · 共 {schedule.entries.length} 条</div>
      <div style={{position:'absolute',left:layout.left,top:layout.top-16,width:layout.width,height:3,background:'#cbb8d9'}}/>
      {layout.items.map(({row,left,top,width:rowWidth,height:rowHeight,dateWidth,gap,title},i)=><div key={row.id || i} data-schedule-row={row.id || i} style={{position:'absolute',left,top,width:rowWidth,height:rowHeight-1,borderBottom:'2px solid #e1d4e8',display:'flex',gap,boxSizing:'border-box',paddingTop:5}}>
        <div style={{width:dateWidth,flexShrink:0,position:'relative',paddingTop:1}}>
          <div style={{position:'relative',width:dateWidth,height:44,display:'flex',alignItems:'center',justifyContent:'center',fontStyle:'italic',fontFamily:'Georgia,serif',fontSize:29}}><Brush/><span style={{position:'relative'}}>{weekdays[new Date(row.date+'T00:00:00Z').getUTCDay()]}</span></div>
          <div style={{fontSize:23,textAlign:'center',marginTop:4,color:'#9b80ad'}}>{shortDate(row.date)}</div>
          {i===0 && <svg viewBox="0 0 190 100" style={{position:'absolute',left:-7,top:-8,width:dateWidth+14,height:61,transform:'rotate(-14deg)'}}><ellipse cx="94" cy="50" rx="84" ry="37" stroke="#d886ce" strokeWidth="5" fill="none"/></svg>}
        </div>
        <div style={{minWidth:0,flex:1}}><div style={{fontSize:23,color:'#a080b2',lineHeight:'28px',whiteSpace:'nowrap'}}>{row.time_label}</div>
          <div data-schedule-title style={{fontSize:title.fontSize,fontWeight:500,lineHeight:1.22,whiteSpace:'pre-wrap'}}>{title.lines.join('\n')}</div>
        </div>
      </div>)}
      <div style={{position:'absolute',left:layout.left,top:951,fontSize:23,color:'#a189b0'}}>北京时间 · 安排调整以主播最新公告为准</div>
      <div style={{position:'absolute',right:111,top:951,fontSize:23,color:'#9b78ac'}}>下次直播见 ♡</div>
    </div>
  </AbsoluteFill>;
};
