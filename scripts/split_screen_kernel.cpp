// Reuse the audited count encoding; permit unequal, fixed chronological blocks.
#include "curveball_power.cpp"
static bool valid(const std::vector<Row>& rows,const int* ends,int blocks) {
    if(rows.empty() || rows.size()>65535 || blocks<1)return false;
    int start=0;
    for(int k=0;k<blocks;++k){if(ends[k]-start<2 || ends[k]>(int)rows.size())return false;start=ends[k];}
    if(start!=(int)rows.size())return false;
    for(Row r:rows)if((r>>45)||__builtin_popcountll(r)!=6)return false;
    return true;
}
static void partition_walk(std::vector<Row>& rows,int steps,const int* ends,int blocks,std::mt19937_64& rng){
    for(int s=0;s<steps;++s){
        int k=bounded(rng,blocks),start=k?ends[k-1]:0,n=ends[k]-start;
        int i=start+bounded(rng,n),j=start+bounded(rng,n-1);if(j>=i)++j;
        Row a=rows[i],b=rows[j],common=a&b,diff=a^b,picked=0;
        int need=__builtin_popcountll(a&~common),size=0;std::array<int,12> values;
        while(diff){values[size++]=__builtin_ctzll(diff);diff&=diff-1;}
        for(int x=0;x<need;++x){int y=x+bounded(rng,size-x);std::swap(values[x],values[y]);picked|=Row(1)<<values[x];}
        rows[i]=common|picked;rows[j]=common|((a^b)&~picked);
    }
}
static bool partition_margins(const std::vector<Row>& a,const std::vector<Row>& b,const int* ends,int blocks){
    int start=0;
    for(int k=0;k<blocks;++k){int delta[45]{};
        for(int i=start;i<ends[k];++i){if(__builtin_popcountll(b[i])!=6)return false;
            Row x=a[i],y=b[i];while(x){++delta[__builtin_ctzll(x)];x&=x-1;}while(y){--delta[__builtin_ctzll(y)];y&=y-1;}}
        for(int v:delta)if(v)return false;start=ends[k];}
    return true;
}
API int split_hub(const Row* input,int n,const int* ends,int blocks,int steps,std::uint64_t seed,Row* out){
    if(n<1||steps<0)return 1;std::vector<Row> rows(input,input+n);
    if(!valid(rows,ends,blocks))return 2;auto initial=rows;std::mt19937_64 rng(seed);
    partition_walk(rows,steps,ends,blocks,rng);if(!partition_margins(initial,rows,ends,blocks))return 3;
    std::copy(rows.begin(),rows.end(),out);return 0;
}
API int split_spokes(const Row* hub,int n,const int* ends,int blocks,int reps,int steps,std::uint64_t seed,std::uint16_t* out){
    if(n<1||steps<0||reps<1)return 1;std::vector<Row> initial(hub,hub+n);
    if(!valid(initial,ends,blocks))return 2;std::mt19937_64 rng(seed);
    for(int s=0;s<reps;++s){auto rows=initial;partition_walk(rows,steps,ends,blocks,rng);
        if(!partition_margins(initial,rows,ends,blocks))return 3;counts(rows,out+s*15180);}
    return 0;
}
