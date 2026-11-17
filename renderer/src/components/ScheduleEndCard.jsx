import React from 'react';
import {AbsoluteFill, Img, useCurrentFrame, useVideoConfig} from 'remotion';

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
  const {width,height,fps} = useVideoConfig(), frame = useCurrentFrame();
  const k = Math.min(width/1920,height/1080), all=schedule.entries.slice(0,8), pages=Math.ceil(all.length/2);
  const page=Math.min(pages-1,Math.floor(frame/(7*fps))), rows=all.slice(page*2,page*2+2);
  const end=new Date(schedule.week_start+'T00:00:00Z');end.setUTCDate(end.getUTCDate()+6);
  const weekLabel=shortDate(schedule.week_start)+' — '+shortDate(end.toISOString().slice(0,10));
  return <AbsoluteFill style={{background:'#ded4e6',fontFamily:'"Noto Sans CJK SC","Microsoft YaHei",sans-serif',color:PURPLE}}>
    <div style={{position:'absolute',left:'50%',top:'50%',width:1920,height:1080,transform:`translate(-50%,-50%) scale(${k})`}}>
      <div style={{position:'absolute',inset:24,borderRadius:18,background:'#75648e',boxShadow:'0 9px 22px #3e304d70'}}/>
      <div style={{position:'absolute',inset:43,borderRadius:12,border:'3px dashed #c3b4d4'}}/>
      <div style={{position:'absolute',left:64,top:68,width:1792,height:944,border:'5px solid #c4b0d4',boxSizing:'border-box',
        backgroundColor:'#fcfafc',backgroundImage:'linear-gradient(#ad99bc13 2px,transparent 2px),linear-gradient(90deg,#ad99bc13 2px,transparent 2px)',backgroundSize:'52px 52px'}}/>
      <div style={{position:'absolute',left:90,top:86,width:638,height:880,transform:'rotate(-9deg)',background:'repeating-linear-gradient(0deg,#d1c6dc,#d1c6dc 4px,#d7cde0 4px,#d7cde0 8px)',boxShadow:'3px 6px 12px #53426045'}}/>
      <div style={{position:'absolute',left:157,top:74,width:247,height:65,transform:'rotate(-13deg)',background:'#ccb9dcaa',borderLeft:'3px dashed #f3e9fa',borderRight:'3px dashed #f3e9fa'}}/>
      <Portrait schedule={schedule} assetBase={assetBase}/>
      <Heart style={{position:'absolute',left:633,top:66,width:110,height:110,transform:'rotate(18deg)'}}/>
      <div style={{position:'absolute',left:798,top:117,fontSize:26,letterSpacing:7,color:'#b49bc4'}}>WEEKLY STREAM DIARY</div>
      <div style={{position:'absolute',left:790,top:163,fontSize:80,fontWeight:600,letterSpacing:7}}>本周后续日程</div>
      <div style={{position:'absolute',right:108,top:99,textAlign:'right',color:'#a387b8',fontSize:28,lineHeight:1.7}}>{weekLabel}<br/>北京时间 · {page+1} / {pages}</div>
      <div style={{position:'absolute',left:800,top:276,width:986,height:3,background:'#cbb8d9'}}/>
      {rows.map((row,i)=><div key={row.id} style={{position:'absolute',left:799,top:330+i*285,width:991,height:242,borderBottom:'2px solid #e1d4e8',display:'flex',gap:34}}>
        <div style={{width:160,flexShrink:0,position:'relative',paddingTop:7}}>
          <div style={{position:'relative',width:160,height:76,display:'flex',alignItems:'center',justifyContent:'center',fontStyle:'italic',fontFamily:'Georgia,serif',fontSize:40}}><Brush/><span style={{position:'relative'}}>{weekdays[new Date(row.date+'T00:00:00Z').getUTCDay()]}</span></div>
          <div style={{fontSize:30,textAlign:'center',marginTop:17,color:'#9b80ad'}}>{shortDate(row.date)}</div>
          {i===0 && <svg viewBox="0 0 190 100" style={{position:'absolute',left:-17,top:-6,width:190,height:100,transform:'rotate(-14deg)'}}><ellipse cx="94" cy="50" rx="84" ry="37" stroke="#d886ce" strokeWidth="5" fill="none"/><path d="M141 18Q178 21 175 53" stroke="#ecc0e6" strokeWidth="3" fill="none"/></svg>}
        </div>
        <div style={{minWidth:0,flex:1}}><div style={{fontSize:36,color:'#a080b2',marginBottom:16}}>{row.time_label}</div>
          <div style={{fontSize:58,fontWeight:500,lineHeight:1.4,display:'-webkit-box',WebkitLineClamp:2,WebkitBoxOrient:'vertical',overflow:'hidden',overflowWrap:'anywhere'}}>{clipped(row.title,26)}</div>
        </div>
      </div>)}
      <div style={{position:'absolute',left:819,top:929,fontSize:27,color:'#a189b0'}}>完整安排见作品简介 · 调整以主播最新公告为准</div>
      <div style={{position:'absolute',right:89,bottom:84,width:310,padding:'20px 25px',background:'#fffdf3',transform:'rotate(-6deg)',
        boxShadow:'2px 6px 12px #59466b36',fontSize:25,lineHeight:1.7,color:'#8b7995',boxSizing:'border-box'}}>下次直播见 ♡<br/>{schedule.more_count ? `还有 ${schedule.more_count} 条，见简介` : '记得来直播间玩呀'}</div>
      <svg viewBox="0 0 120 120" style={{position:'absolute',left:677,top:864,width:90,height:90}} fill="none"><path d="M60 8L70 46 110 60 70 71 60 112 47 71 9 60 47 46Z" stroke="#ad8cbd" strokeWidth="3"/></svg>
    </div>
  </AbsoluteFill>;
};
