// Batched exhaustive scan of distinct first-draw preimages, same ticket generator.
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <thread>
#include <vector>
using U = uint64_t;
#ifndef MIN_HITS
#define MIN_HITS 3
#endif
constexpr U N=8145060, STEP=0x9e3779b97f4a7c15ULL;
// Exact divisibility test: N = 4 * odd. Rotate the modular product so
// divisibility by four is included. Guard subtraction against uint64 wrap.
bool is_target(U value,U target) {
    U product=(value-target)*7802328685912566297ULL;
    U rotated=(product>>2)|(product<<62);
    return value>=target && rotated<=UINT64_MAX/N;
}
U mix(U x) {
    x=(x^(x>>30))*0xbf58476d1ce4e5b9ULL;
    x=(x^(x>>27))*0x94d049bb133111ebULL;
    return x^(x>>31);
}
U unmix(U x) {
    x=x^(x>>31)^(x>>62);
    x*=0x319642b2d24d8ec3ULL;
    x=x^(x>>27)^(x>>54);
    x*=0x96de1b173f119089ULL;
    return x^(x>>30)^(x>>60);
}
struct Survivor { U seed; unsigned hits; };
int main(int argc,char**) {
    if(argc>1) {
        U value,target;
        while(std::cin>>value>>target) std::cout<<is_target(value,target)<<'\n';
        return 0;
    }
    U anchor, target, start, count, offset, stride;
    unsigned rounds, workers;
    if (!(std::cin>>rounds>>anchor>>target>>start>>count>>offset>>stride>>workers)) return 2;
    if (!rounds || rounds>10000 || !workers || workers>64 || target>=N) return 2;
    U population=(UINT64_MAX-target)/N+1;
    if (!count || start>=population || count>population-start || offset>=population ||
        !stride || stride>=population || std::gcd(stride,population)!=1) return 2;
    std::vector<U> steps(rounds), targets(rounds);
    for(unsigned i=0;i<rounds;i++) {
        U draw;
        if(!(std::cin>>draw>>targets[i]) || !draw || targets[i]>=N) return 2;
        steps[i]=draw*STEP;
    }
    std::vector<std::vector<U>> hist(workers,std::vector<U>(rounds+1,0));
    std::vector<std::vector<Survivor>> found(workers);
    std::vector<std::thread> threads;
    for(unsigned t=0;t<workers;t++) threads.emplace_back([&,t]{
        U begin=count*t/workers, end=count*(t+1)/workers;
        U q=(U)(((unsigned __int128)(start+begin)*stride+offset)%population);
        for(U j=begin;j<end;j++) {
            U seed=unmix(q*N+target)-anchor*STEP;
            unsigned hits=0;
            for(unsigned i=0;i<rounds;i++) hits+=is_target(mix(seed+steps[i]),targets[i]);
            hist[t][hits]++;
            if(hits>=MIN_HITS) found[t].push_back({seed,hits});
            q+=stride;
            if(q>=population) q-=population;
        }
    });
    for(auto &thread:threads) thread.join();
    std::vector<U> totals(rounds+1,0);
    std::vector<Survivor> survivors;
    for(unsigned t=0;t<workers;t++) {
        for(unsigned i=0;i<=rounds;i++) totals[i]+=hist[t][i];
        survivors.insert(survivors.end(),found[t].begin(),found[t].end());
    }
    std::sort(survivors.begin(),survivors.end(),[](auto a,auto b){return a.seed<b.seed;});
    std::cout<<"{\"histogram\":[";
    for(unsigned i=0;i<=rounds;i++) std::cout<<(i?",":"")<<totals[i];
    std::cout<<"],\"survivors\":[";
    for(size_t i=0;i<survivors.size();i++)
        std::cout<<(i?",":"")<<"{\"seed\":\""<<survivors[i].seed<<"\",\"hits\":"<<survivors[i].hits<<"}";
    std::cout<<"]}\n";
}
