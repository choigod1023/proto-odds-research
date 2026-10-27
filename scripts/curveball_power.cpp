// Same reversible Curveball kernel as the Python study, executed on one thread.
#include <algorithm>
#include <array>
#include <cstdint>
#include <random>
#include <vector>
#ifdef _WIN32
#define API extern "C" __declspec(dllexport)
#else
#define API extern "C"
#endif
using Row=std::uint64_t;
static unsigned bounded(std::mt19937_64& rng,unsigned high) {
    return std::uniform_int_distribution<unsigned>(0,high-1)(rng);
}
static void walk(std::vector<Row>& rows,int steps,int block,std::mt19937_64& rng) {
    for(int s=0;s<steps;++s) {
        int base=block*bounded(rng,rows.size()/block);
        int i=bounded(rng,block),j=bounded(rng,block-1);if(j>=i)++j;
        Row a=rows[base+i],b=rows[base+j],common=a&b,diff=a^b;
        int needed=__builtin_popcountll(a&~common),size=0;
        std::array<int,12> values;
        while(diff){int v=__builtin_ctzll(diff);values[size++]=v;diff&=diff-1;}
        Row picked=0;
        for(int k=0;k<needed;++k){int j=k+bounded(rng,size-k);std::swap(values[k],values[j]);picked|=Row(1)<<values[k];}
        rows[base+i]=common|picked;rows[base+j]=common|((a^b)&~picked);
    }
}
static void counts(const std::vector<Row>& rows,std::uint16_t* out) {
    std::fill(out,out+15180,0);
    // lexicographic pair/triple IDs, matching Python itertools.combinations.
    static const auto pair=[](){std::array<int,45*45> x{};int id=0;
        for(int a=0;a<45;++a)for(int b=a+1;b<45;++b)x[a*45+b]=id++;return x;}();
    static const auto triple=[](){std::array<int,45*45*45> x{};int id=990;
        for(int a=0;a<45;++a)for(int b=a+1;b<45;++b)for(int c=b+1;c<45;++c)x[(a*45+b)*45+c]=id++;return x;}();
    for(Row r:rows){int v[6],n=0;while(r){v[n++]=__builtin_ctzll(r);r&=r-1;}
        for(int a=0;a<6;++a)for(int b=a+1;b<6;++b){++out[pair[v[a]*45+v[b]]];
            for(int c=b+1;c<6;++c)++out[triple[(v[a]*45+v[b])*45+v[c]]];}}
}
static bool same_margins(const std::vector<Row>& before,const std::vector<Row>& after,int block) {
    for(int start=0;start<(int)before.size();start+=block){
        int difference[45]{};
        for(int i=start;i<start+block;++i){
            if(__builtin_popcountll(after[i])!=6)return false;
            Row a=before[i],b=after[i];
            while(a){++difference[__builtin_ctzll(a)];a&=a-1;}
            while(b){--difference[__builtin_ctzll(b)];b&=b-1;}
        }
        for(int value:difference)if(value)return false;
    }
    return true;
}
API int endpoint(const std::uint64_t* input,int n,int steps,int block,std::uint64_t seed,std::uint64_t* output) {
    if(n<=0||block<2||n%block||steps<0)return 1;
    std::vector<Row> rows(input,input+n);
    for(Row r:rows)if((r>>45)||__builtin_popcountll(r)!=6)return 2;
    std::mt19937_64 rng(seed);walk(rows,steps,block,rng);std::copy(rows.begin(),rows.end(),output);return 0;
}
API int spoke_counts(const std::uint64_t* hub,int n,int reps,int steps,int block,std::uint64_t seed,std::uint16_t* out) {
    if(n<=0||block<2||n%block||steps<0||reps<1)return 1;
    std::vector<Row> initial(hub,hub+n);
    for(Row r:initial)if((r>>45)||__builtin_popcountll(r)!=6)return 2;
    std::mt19937_64 rng(seed);
    for(int s=0;s<reps;++s){auto rows=initial;walk(rows,steps,block,rng);
        if(!same_margins(initial,rows,block))return 3;
        counts(rows,out+s*15180);}
    return 0;
}
