"""Explicit research-only aliases; no fuzzy or outcome-assisted team matching."""
from scripts.join_tipster_expansion import ALIASES
from scripts.collect_tipster_year_nba import NAMES

NBA = {
 'ATL':['애틀호크','Hawks','Atlanta'],'BOS':['보스셀틱','Celtics','Boston'],
 'BKN':['브루네츠','Nets','Brooklyn'],'CHA':['샬럿호네','Hornets','Charlotte'],
 'CHI':['시카불스','Bulls','Chicago'],'CLE':['클리캐벌','Cavaliers','Cleveland'],
 'DAL':['댈러매버','Mavericks','Dallas'],'DEN':['덴버너게','Nuggets','Denver'],
 'DET':['디트피스','Pistons','Detroit'],'GSW':['골든워리','Warriors','Golden State'],
 'HOU':['휴스로케','Rockets','Houston'],'IND':['인디페이','Pacers','Indiana'],
 'LAC':['LA클리퍼','Clippers','LA Clippers'],'LAL':['LA레이커','Lakers','LA Lakers'],
 'MEM':['멤피그리','Grizzlies','Memphis'],'MIA':['마이히트','Heat','Miami'],
 'MIL':['밀워벅스','Bucks','Milwaukee'],'MIN':['미네울브','Timberwolves','Minnesota'],
 'NOP':['뉴올펠리','Pelicans','New Orleans'],'NYK':['뉴욕닉스','Knicks','New York','NY Knicks'],
 'OKC':['오클썬더','Thunder','Oklahoma City'],'ORL':['올랜매직','Magic','Orlando'],
 'PHI':['필라76s','76ers','Philadelphia'],'PHX':['피닉선즈','Suns','Phoenix'],
 'POR':['포틀트레','Trail Blazers','Portland'],'SAC':['새크킹스','Kings','Sacramento'],
 'SAS':['샌안스퍼','Spurs','San Antonio'],'TOR':['토론랩터','Raptors','Toronto'],
 'UTA':['유타재즈','Jazz','Utah'],'WAS':['워싱위저','Wizards','Washington'],
}

MLB = {
 'Arizona Diamondbacks':['애리다이','Diamondbacks','Arizona'],
 'Athletics':['애슬레틱','Oakland Athletics','Sacramento Athletics','Athletics'],
 'Atlanta Braves':['애틀브레','Braves','Atlanta'], 'Baltimore Orioles':['볼티오리','Orioles','Baltimore'],
 'Boston Red Sox':['보스레드','Red Sox','Boston'], 'Chicago Cubs':['시카컵스','Cubs'],
 'Chicago White Sox':['시카화이','White Sox'], 'Cincinnati Reds':['신시레즈','Reds','Cincinnati'],
 'Cleveland Guardians':['클리가디','Guardians','Cleveland'], 'Colorado Rockies':['콜로로키','Rockies','Colorado'],
 'Detroit Tigers':['디트타이','Tigers','Detroit'], 'Houston Astros':['휴스애스','Astros','Houston'],
 'Kansas City Royals':['캔자로얄','Royals','Kansas City'], 'Los Angeles Angels':['LA에인절','Angels','LA Angels'],
 'Los Angeles Dodgers':['LA다저스','Dodgers','LA Dodgers'], 'Miami Marlins':['마이말린','Marlins','Miami'],
 'Milwaukee Brewers':['밀워브루','Brewers','Milwaukee'], 'Minnesota Twins':['미네트윈','Twins','Minnesota'],
 'New York Mets':['뉴욕메츠','Mets','NY Mets'], 'New York Yankees':['뉴욕양키','Yankees','NY Yankees'],
 'Philadelphia Phillies':['필라필리','Phillies','Philadelphia'], 'Pittsburgh Pirates':['피츠파이','Pirates','Pittsburgh'],
 'San Diego Padres':['샌디파드','Padres','San Diego'], 'San Francisco Giants':['샌프자이','Giants','San Francisco'],
 'Seattle Mariners':['시애매리','Mariners','Seattle'], 'St. Louis Cardinals':['세인카디','Cardinals','St Louis Cardinals','St. Louis'],
 'Tampa Bay Rays':['탬파레이','Rays','Tampa Bay'], 'Texas Rangers':['텍사레인','Rangers','Texas'],
 'Toronto Blue Jays':['토론블루','Blue Jays','Toronto'], 'Washington Nationals':['워싱내셔','Nationals','Washington'],
}


def configure():
    for code, aliases in NBA.items():
        ALIASES['NBA'].setdefault(NAMES[code], []).extend(aliases+[code])
    for target, aliases in MLB.items():
        ALIASES['MLB'].setdefault(target, []).extend(aliases)
    for target, aliases in {'울산':['울산HDFC'],'제주':['제주SKFC'],
                           '대전':['대전하나'],'포항':['포항스틸'],'인천':['인천유나']}.items():
        ALIASES['K리그1'].setdefault(target, []).extend(aliases)
    for target, aliases in {'부산':['부산아이'],'서울E':['서울이랜','서울이랜드','서울이랜드FC'],
        '안산':['안산그리','안산그리너스','안산그리너스FC'],'전남':['전남드래'],
        '충남아산':['충남아산FC'],'인천':['인천유나','인천유나이티드']}.items():
        ALIASES['K리그2'].setdefault(target, []).extend(aliases)
