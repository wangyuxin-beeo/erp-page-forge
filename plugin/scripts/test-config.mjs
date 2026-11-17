import {Config} from '../lib/config.js'
import assert from 'node:assert/strict'
const validate=Config['~standard'].validate
assert.deepEqual(validate({}).value,{python:'',timeoutSec:300})
for(const v of [[],{python:42},{timeoutSec:-1},{timeoutSec:'300'},{timeoutSec:Infinity}])assert(validate(v).issues)
assert.equal(validate({timeoutSec:600}).value.timeoutSec,600)
console.log('PASS Standard Schema defaults and invalid deployment configurations')
